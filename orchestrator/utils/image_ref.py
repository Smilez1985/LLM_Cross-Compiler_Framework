#!/usr/bin/env python3
"""
Docker image references, read without looking anything up (consolidation chain c1-imports: moved unchanged from
orchestrator/llm/task_prompts.py, which keeps the names, so the registry client in utils, the module tools, the pack
recipes and Ditto's module proposals share it from the bottom layer).

- split_image(reference): registry, repository, tag, digest and whether it is a Docker Official Image (on Docker Hub
  without a namespace, e.g. debian:bookworm-slim).
- is_official_image(reference): that last answer as a bool.
(runtime/container_server.is_official_image is another rule: an official image pinned by digest, for Ditto's own
container.)
"""

from typing import Dict


def split_image(reference: str) -> Dict[str, str]:
    """registry, repository, tag and digest of an image reference, and whether it is a Docker Official Image (on
    Docker Hub without a namespace, e.g. debian:bookworm-slim). Nothing is looked up."""
    rest, _, digest = (reference or "").strip().partition("@")
    first, slash, remainder = rest.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, remainder
    else:
        registry, path = "docker.io", rest
    last = path.rsplit("/", 1)[-1]
    tag = last.rpartition(":")[2] if ":" in last else ""
    repository = path[:-(len(tag) + 1)] if tag else path
    if registry == "docker.io" and repository.startswith("library/"):
        repository = repository[len("library/"):]
    official = registry == "docker.io" and bool(repository) and "/" not in repository
    return {"registry": registry, "repository": repository, "tag": tag, "digest": digest,
            "official": "yes" if official else ""}


def is_official_image(reference: str) -> bool:
    return bool(split_image(reference)["official"])


__all__ = ["is_official_image", "split_image"]
