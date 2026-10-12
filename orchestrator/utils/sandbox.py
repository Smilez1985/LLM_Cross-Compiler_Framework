#!/usr/bin/env python3
"""
Container sandbox options shared by the conversion stages (orchestrator/Core/builder.py) and the llama.cpp builder
container (orchestrator/runtime/build/executor.py).

Both run code that is not the framework's own (model tooling, a target's build scripts, upstream's CMake), so the
container gets no network, no capabilities, no privilege escalation, a read-only root file system with a small /tmp,
a process limit, optionally a memory limit, and the host user instead of root.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

FALLBACK_USER = (1000, 1000)  # used when the host user is root or on Windows
# A tmpfs inside the container (not a host path).
TMPFS = {"/tmp": "rw,nosuid,nodev,size=1g"}  # nosec B108


def sandbox_user() -> Tuple[int, int]:
    """The uid:gid a sandboxed container runs as: the host user, or FALLBACK_USER for root and on Windows."""
    if sys.platform != "win32" and os.getuid() != 0:
        return os.getuid(), os.getgid()
    return FALLBACK_USER


def hand_over(path: Path, user: Tuple[int, int]) -> None:
    """A root host owns the folders it created; the container's fallback user needs to write them."""
    if sys.platform == "win32" or os.getuid() != 0:
        return
    for item in [path, *Path(path).rglob("*")]:
        try:
            os.chown(item, *user)
        except OSError:
            pass


def container_sandbox(user: Tuple[int, int], *, working_dir: str, pids_limit: int = 4096,
                      mem_limit: Optional[str] = None) -> Dict[str, Any]:
    """docker-py create() keyword arguments of the sandbox."""
    options: Dict[str, Any] = {
        "user": f"{user[0]}:{user[1]}",
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": dict(TMPFS),
        "pids_limit": int(pids_limit),
        "working_dir": working_dir,
    }
    if mem_limit:
        options["mem_limit"] = mem_limit
    return options
