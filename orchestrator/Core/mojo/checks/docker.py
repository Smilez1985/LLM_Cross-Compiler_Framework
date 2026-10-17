#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the Docker daemon, buildx and where Docker keeps its images
"""

from typing import List

from orchestrator.Core.mojo.docker_objects import docker_storage
from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_docker(framework) -> List[Finding]:
    from orchestrator.utils.validation import check_docker_environment
    client = framework.get_component("docker_client") if hasattr(framework, "get_component") else None
    docker = check_docker_environment(client)   # the build engine's check (round 12: one Docker check)
    if not docker.reachable:
        return [Finding("docker", "docker.daemon", Severity.ERROR, "The Docker daemon is not reachable; builds cannot run.",
                        "Start Docker (Docker Desktop on Windows/macOS, 'sudo systemctl start docker' on Linux) and make "
                        "sure your user may use it (docker group or rootless Docker).",
                        where="the Docker daemon", why="Builds cross-compile inside Docker, so no build can run "
                                                       "without it.", cannot="admin")]
    findings = [_ok("docker", "docker.daemon", "Docker daemon reachable")]
    if docker.buildx is True:
        findings.append(_ok("docker", "docker.buildx", "BuildKit (buildx) available"))
    elif docker.buildx is False:
        findings.append(Finding("docker", "docker.buildx", Severity.WARN,
                                "docker buildx is missing; image builds fall back to the slower legacy builder.",
                                "Install the Docker buildx plugin.", where="the docker command",
                                why="Image builds take longer and reuse less of earlier builds.",
                                cannot="admin"))
    findings.extend(_storage_findings(framework))
    return findings


def _storage_findings(framework) -> List[Finding]:
    """Where Docker keeps its images and how much the framework's images use (round 7 item 12): for the person's
    overview; moving Docker's data folder is an administrator's step the framework never takes."""
    storage = docker_storage(framework)
    if not storage or not storage.get("root"):
        return []
    from orchestrator.Core.deletion import size_text
    free = storage.get("free_bytes")
    where = storage["root"] + (f" ({size_text(free)} free on that disk)" if isinstance(free, int) else "")
    return [Finding("docker", "docker.storage", Severity.INFO,
                    f"Docker keeps its images in {where}; the framework's {storage['images']} build images use about "
                    f"{size_text(storage['image_bytes'])} (layers shared between images are counted for each). "
                    "The framework never deletes an image by itself.",
                    "Tools > Framework Build Images... or 'llm-cli doctor --fix --tier 2' lists them for you to "
                    "delete the ones you choose. To keep Docker's images on another disk, an administrator moves "
                    "Docker's data folder (see docs/Container_Setup.md); the framework does not change it.")]
