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
    from orchestrator.utils.docker_engine import STALE_PODMAN_HOST, STALE_PODMAN_WAY, stale_podman_host
    stale = ([Finding("docker", "docker.host_stale", Severity.WARN, STALE_PODMAN_HOST, STALE_PODMAN_WAY,
                      where="the DOCKER_HOST setting of your shell",
                      why="With it the framework uses Podman although Docker runs here, so GPU builds and BuildKit "
                          "features are refused.", cannot="person")]
             if stale_podman_host() else [])
    if docker.engine == "podman":
        # The fallback of the RHEL family (Fedora, RHEL, CentOS Stream and the RHEL rebuilds): it builds, with limits.
        from orchestrator.utils.docker_engine import DOCKER_CE_WAY, PODMAN_LIMITS
        return [_ok("docker", "docker.daemon", "Container engine reachable: Podman, through its Docker-compatible "
                                               "interface (builds run there)"),
                Finding("docker", "docker.engine", Severity.WARN,
                        "The container engine is Podman, not Docker: " + PODMAN_LIMITS["gpu"] + "; and "
                        + PODMAN_LIMITS["buildkit"] + ". The framework refuses these with the reason.",
                        DOCKER_CE_WAY, where="the container engine",
                        why="Docker CE is the framework's standard engine; under Podman, GPU builds and BuildKit "
                            "features are not available.", cannot="admin")] + stale \
            + _stage_user_findings(framework, client) + _storage_findings(framework)
    findings = [_ok("docker", "docker.daemon", "Docker daemon reachable (container engine: Docker)")] + stale
    findings.extend(_stage_user_findings(framework, client))
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


def _stage_user_findings(framework, client) -> List[Finding]:
    """Whether the build sandbox can run the stage modules under their own user on this computer (utils/sandbox.py
    stage_user_support, the builder's own rule): plain words, no path and no account name."""
    from orchestrator.utils.sandbox import STAGE_USER_NO_SUBIDS_WAY, stage_user_support
    config = getattr(framework, "config", None)
    getter = getattr(config, "get", None)
    try:
        sandbox_on = bool(getter("build_sandbox", True)) if callable(getter) else True
    except Exception:  # noqa: BLE001 - an unreadable setting counts as the default (on)
        sandbox_on = True
    supported, text = stage_user_support(client, sandbox_on=sandbox_on)
    if supported:
        return [_ok("docker", "docker.stage_user", text)]
    if supported is False:
        return [Finding("docker", "docker.stage_user", Severity.ERROR, text, STAGE_USER_NO_SUBIDS_WAY,
                        where="the rootless container engine", cannot="admin",
                        why="The stage modules must run under their own user so that they cannot read the stage "
                            "runner's key; without it every build is refused.")]
    if not sandbox_on:
        return [Finding("docker", "docker.stage_user", Severity.WARN, text,
                        "Switch the setting build_sandbox on.",
                        where="the setting build_sandbox", cannot="person",
                        why="Without the build sandbox the end record of a build is guarded by its seal and the exit "
                            "status only.")]
    return [Finding("docker", "docker.stage_user", Severity.INFO, text)]


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
