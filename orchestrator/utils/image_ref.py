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


def registry_host(reference: str) -> str:
    """The host a pull of `reference` goes to (Docker Hub's registry for an image without a registry)."""
    registry = split_image(reference)["registry"]
    return "registry-1.docker.io" if registry == "docker.io" else registry


def offline_pull_refusal(reference: str, config) -> str:
    """In offline mode (the framework's one offline check) a pull of an image that is not on this computer goes only
    to a registry the one locality rule counts as local (utils/locality.py: this computer, the own network, the
    person's opt-outs); otherwise the plain refusal naming the image and what to do while online. "" when the pull may
    go (offline mode off, or a local registry)."""
    from orchestrator.utils.locality import OPT_OUT_HELPS, check_host, offline_mode, url_host
    if not offline_mode(config):
        return ""
    verdict = check_host(url_host(registry_host(reference))[0], config)   # host[:port], [v6]:port
    if verdict.local:
        return ""
    text = (f"Offline mode is on, and the image {reference} is not on this computer; it would be pulled from "
            f"{verdict.host}, which is not in your own network ({verdict.why()}). Pull it once while offline mode is "
            "off (or package without Docker images), then package again offline.")
    if verdict.reason in OPT_OUT_HELPS:   # as the locality rule's own refusal (Verdict.refusal)
        from orchestrator.utils.localization import ui_text
        text += ui_text("net.refusal.opt_out", "en")
    return text


__all__ = ["is_official_image", "offline_pull_refusal", "registry_host", "split_image"]
