#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the Docker daemon, buildx, the rights the engine gives your account
(rootless, or a group with root effect) and where Docker keeps its images
"""

from typing import List

from orchestrator.Core.mojo.docker_objects import docker_storage
from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_docker(framework) -> List[Finding]:
    from orchestrator.utils.validation import check_docker_environment
    client = framework.get_component("docker_client") if hasattr(framework, "get_component") else None
    docker = check_docker_environment(client)   # the build engine's check (round 12: one Docker check)
    if not docker.reachable:
        return [Finding("docker", "docker.daemon", Severity.ERROR,
                        "The Docker daemon is not reachable; builds cannot run.",
                        "Start the container engine (Podman Desktop or Docker Desktop on Windows/macOS; on Linux your "
                        "rootless Podman: 'systemctl --user enable --now podman.socket', or 'sudo ./install.sh', which "
                        "sets up rootless Podman by default).",
                        where="the Docker daemon", why="Builds cross-compile inside Docker, so no build can run "
                                                       "without it.", cannot="admin")]
    from orchestrator.utils.docker_engine import STALE_PODMAN_HOST, STALE_PODMAN_WAY, stale_podman_host
    stale = ([Finding("docker", "docker.host_stale", Severity.WARN, STALE_PODMAN_HOST, STALE_PODMAN_WAY,
                      where="the DOCKER_HOST setting of your shell",
                      why="With it the framework uses Podman although your rootless Docker runs here, so GPU builds "
                          "and BuildKit "
                          "features are refused.", cannot="person")]
             if stale_podman_host() else [])
    privilege = _privilege_findings(client)
    if docker.engine == "podman":
        # Rootless Podman is the default engine install.sh sets up: it builds, with limits named here.
        from orchestrator.utils.docker_engine import DOCKER_CE_WAY, PODMAN_LIMITS
        return [_ok("docker", "docker.daemon", "Container engine reachable: Podman, through its Docker-compatible "
                                               "interface (builds run there)"),
                Finding("docker", "docker.engine", Severity.INFO,
                        "The container engine is Podman: " + PODMAN_LIMITS["gpu"] + "; and "
                        + PODMAN_LIMITS["buildkit"] + ". The framework refuses these with the reason.",
                        DOCKER_CE_WAY, where="the container engine",
                        why="Under Podman, GPU builds and BuildKit features are not available; everything else "
                            "builds.", cannot="admin")] + privilege + stale \
            + _stage_user_findings(framework, client) + _storage_findings(framework)
    findings = [_ok("docker", "docker.daemon", "Docker daemon reachable (container engine: Docker)")]
    findings += privilege + stale
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


# Mojo's lines about the rights the container engine gives the person's account (no standing rights that can do more
# than needed, docs/DESIGN.md). Fixed texts: no account name, no path, so an AI reads the same verdict.
ROOTLESS_OK = "The container engine runs rootless: it builds with your own rights only."
ROOTFUL_TEXT = ("The container engine runs with administrator (root) rights. The framework prefers a rootless engine "
                "as soon as one is set up for your account.")


def _privilege_findings(client, platform=None) -> List[Finding]:
    """Whether the engine runs rootless, and the group with root effect the person's account is in (Linux 'docker',
    Windows 'docker-users'), named plainly with what it means and the way to a rootless engine."""
    import sys
    from orchestrator.utils.docker_engine import (ROOT_GROUP_TEXT, ROOT_GROUP_WAY, ROOT_GROUP_WHY, root_effect_group,
                                                  rootless_docker, rootless_podman)
    platform = platform or sys.platform
    findings: List[Finding] = []
    linux = platform.startswith("linux")
    rootless = linux and client is not None and (rootless_docker(client) or rootless_podman(client))
    group = root_effect_group(platform)
    if rootless:
        findings.append(_ok("docker", "docker.rootless", ROOTLESS_OK))
    elif linux and client is not None and not group:
        findings.append(Finding("docker", "docker.rootless", Severity.INFO, ROOTFUL_TEXT,
                                ROOT_GROUP_WAY["linux"].split(", then an administrator", 1)[0] + ".",
                                where="the container engine", cannot="admin", steps=("docker.rootless",)))
    if group:
        way = ROOT_GROUP_WAY["win32" if platform.startswith("win") else "linux"]
        findings.append(Finding("docker", "docker.root_group", Severity.WARN, ROOT_GROUP_TEXT.format(group=group),
                                way, where=f"the group '{group}' of your account", why=ROOT_GROUP_WHY,
                                cannot="admin", steps=("docker.rootless",)))
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
