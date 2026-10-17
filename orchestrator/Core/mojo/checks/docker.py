#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the Docker daemon, buildx and where Docker keeps its images
"""

import shutil
import subprocess
from typing import List

from orchestrator.Core.mojo.docker_objects import docker_storage
from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_docker(framework) -> List[Finding]:
    client = framework.get_component("docker_client") if hasattr(framework, "get_component") else None
    findings = []
    try:
        if client is None:
            raise RuntimeError("no client")
        client.ping()
        findings.append(_ok("docker", "docker.daemon", "Docker daemon reachable"))
    except Exception:
        return [Finding("docker", "docker.daemon", Severity.ERROR, "The Docker daemon is not reachable; builds cannot run.",
                        "Start Docker (Docker Desktop on Windows/macOS, 'sudo systemctl start docker' on Linux) and make "
                        "sure your user may use it (docker group or rootless Docker).",
                        where="the Docker daemon", why="Builds cross-compile inside Docker, so no build can run "
                                                       "without it.", cannot="admin")]
    if shutil.which("docker"):
        try:
            buildx = subprocess.run(["docker", "buildx", "version"], capture_output=True, text=True, timeout=15)
            if buildx.returncode == 0:
                findings.append(_ok("docker", "docker.buildx", "BuildKit (buildx) available"))
            else:
                findings.append(Finding("docker", "docker.buildx", Severity.WARN,
                                        "docker buildx is missing; image builds fall back to the slower legacy builder.",
                                        "Install the Docker buildx plugin.", where="the docker command",
                                        why="Image builds take longer and reuse less of earlier builds.",
                                        cannot="admin"))
        except (OSError, subprocess.SubprocessError):
            pass
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
