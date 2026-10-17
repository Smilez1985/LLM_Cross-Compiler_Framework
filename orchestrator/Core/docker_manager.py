#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Docker Manager (v2.3.0)
DIREKTIVE: Goldstandard, robust, thread-safe.

Owns the framework's one BuildEngine (its Docker client) and checks that the Docker daemon answers. Without Qt: the
command line, the MCP server and the GUI use it alike.

Builds run through one path: BuildService -> LLMOrchestrator -> BuildEngine (DECISIONS round 12, item 6). This
manager's older build path (start_build, a monitor thread, stop_build and six Qt signals nothing connected to) was
covered by that path and removed; what only it could do lives on there:
- the CPU and memory of the running build container: `container_resources()` below (Docker's own formulas, from the
  old monitor's `_calculate_cpu_percent`), sampled by BuildEngine while a stage container runs and shown in the
  person's Builder under the progress bar (BuildService.live_resources); the build log warns once when the container
  nears its memory limit;
- the healing proposal after a failed build: LLMOrchestrator._heal_and_retry (the failing stage's log tail goes with
  the error), which asks the person in the GUI (HealingDialog) and the CLI.

Updates v2.3.0:
- Use centralized ConfigManager for Docker images (No Hardcoding).
- Robust initialization (Support for calling with/without framework ref).
"""

import logging
from typing import Any, Dict, Mapping, Optional

# A stage container counts as near its memory limit from this share on (one warning in the build log).
MEMORY_WARN_SHARE = 0.9


def cpu_percent(stats: Mapping[str, Any]) -> float:
    """The container's CPU use in one `docker stats` sample, as `docker stats` shows it: (container CPU delta / host
    CPU delta) x online CPUs x 100, rounded to 2 places (400.0 = four CPUs busy). 0.0 without a delta or data."""
    try:
        cpu_stats = stats['cpu_stats']
        precpu_stats = stats['precpu_stats']
        cpu_delta = cpu_stats['cpu_usage']['total_usage'] - precpu_stats['cpu_usage']['total_usage']
        system_delta = cpu_stats['system_cpu_usage'] - precpu_stats['system_cpu_usage']

        if system_delta > 0.0 and cpu_delta > 0.0:
            online_cpus = cpu_stats.get('online_cpus', len(cpu_stats['cpu_usage'].get('percpu_usage', []))) or 1
            return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)
        return 0.0
    except KeyError:
        return 0.0


def memory_used(memory_stats: Mapping[str, Any]) -> int:
    """The container's memory in bytes as `docker stats` counts it: the usage without the inactive page cache, so
    reading a large model file does not look like memory the build needs. Docker's order (docker/cli
    calculateMemUsageUnixNoCache): total_inactive_file on a cgroup v1 host (which reports inactive_file too, without
    the child cgroups) when it is below the usage, else inactive_file (cgroup v2) when it is below the usage, else
    the usage itself."""
    usage = int(memory_stats.get("usage") or 0)
    details = memory_stats.get("stats") or {}
    for key in ("total_inactive_file", "inactive_file"):
        if key in details:
            cache = int(details.get(key) or 0)
            if cache < usage:
                return usage - cache
    return usage


def container_resources(stats: Optional[Mapping[str, Any]]) -> Optional[Dict[str, float]]:
    """{"cpu_percent", "memory_bytes", "memory_limit_bytes"} of one `docker stats` sample (stream=False), or None when
    the sample has no memory figures (the container has ended). The limit is the container's own, else all memory
    Docker may use (the host's, or the WSL2 / Docker Desktop VM's)."""
    memory = (stats or {}).get("memory_stats") or {}
    if not memory.get("usage"):
        return None
    return {"cpu_percent": cpu_percent(stats), "memory_bytes": float(memory_used(memory)),
            "memory_limit_bytes": float(memory.get("limit") or 0)}


def memory_near_limit(resources: Optional[Mapping[str, float]], share: float = MEMORY_WARN_SHARE) -> bool:
    """Whether a sample of container_resources() uses at least `share` of its memory limit."""
    if not resources:
        return False
    limit = resources.get("memory_limit_bytes") or 0
    return limit > 0 and (resources.get("memory_bytes") or 0) >= share * limit


class DockerManager:
    """
    Holds the framework's BuildEngine (and with it the Docker client) and checks the Docker daemon.
    """

    def __init__(self, config_manager=None, base_dir=None, secrets_manager=None):
        self.logger = logging.getLogger("DockerManager")
        self.framework = None
        self.builder = None
        self.config_manager = config_manager
        self._base_dir = base_dir
        self._secrets_manager = secrets_manager

    def initialize(self, framework_manager=None) -> bool:
        """
        Makes the build engine and checks that the Docker daemon answers. False when the engine cannot be made or
        Docker does not answer (the engine is kept then: builds work as soon as Docker runs).
        Args:
            framework_manager: Optional reference to the main Framework kernel (the engine then reads its settings
                               and reuses its Docker client).
        """
        self.framework = framework_manager

        try:
            # Lazy import to avoid circular dependency
            from orchestrator.Core.builder import BuildEngine
            if framework_manager:
                self.builder = BuildEngine(framework_manager)
            else:
                # One engine for the whole app: the framework hands it to the orchestrator as well.
                self.builder = BuildEngine(self.config_manager, base_dir=self._base_dir,
                                           secrets_manager=self._secrets_manager)
        except Exception as e:
            self.logger.error(f"Failed to initialize BuildEngine: {e}")
            return False

        # Check Docker Connectivity
        try:
            if self.builder and hasattr(self.builder, 'docker_client'):
                self.builder.docker_client.ping()
                self.logger.info("Docker Daemon connectivity established.")
                return True
        except Exception as e:
            self.logger.warning(f"Docker Daemon not reachable: {e}")
            return False

        return True
