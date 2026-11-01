#!/usr/bin/env python3
"""
The projects the framework's one self-build path builds (the pinned source, the recipe, the executor, the
verification, the store and the board package of orchestrator/runtime/build/ and runtime/device/package.py).

llama.cpp was the only one; whisper.cpp (the board bundle's speech recognition) uses the same path, not a copy of
it: the same fetch by commit, the same CPU plan from the board's probe (ggml is the same library in both, so the CPU
options, the C library rule, the static C++ runtime and the builder container are the same), the same sandboxed
cross build without network and the same ELF check. What differs per project is data and lives here: the catalog
pin it is built from, its folder below <state dir>/runtime/, the programs it builds, the name of its licence text in
the board package and the name of that package. The options a project's build must set (and the download options it
must keep off) are the recipe's, next to llama.cpp's (recipe.py WHISPER_DOWNLOAD_OPTIONS_OFF).

Every plan names its project in plan.source["project"]; a plan without one is llama.cpp (every plan written before
whisper.cpp was added), so the build IDs, folders and packages of llama.cpp stay what they were.
"""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Pattern, Tuple

LLAMA_CPP = "llama.cpp"
WHISPER_CPP = "whisper.cpp"


@dataclass(frozen=True)
class Project:
    name: str
    folder: str                                  # <state dir>/runtime/<folder>/ (src, builds, device)
    targets: Mapping[str, Tuple[str, ...]]       # the programs each purpose builds
    program: Pattern[str]                        # a program of the project in bin/
    licence_name: str                            # its licence text in the board package
    package_prefix: str                          # <prefix>-<arch>-<build id[:16]>.tar.gz
    start_script: bool                           # the board package carries llama-server.sh (llama.cpp only)

    @property
    def purposes(self) -> Tuple[str, ...]:
        return tuple(self.targets)


PROJECTS: Mapping[str, Project] = {
    LLAMA_CPP: Project(
        name=LLAMA_CPP, folder="llama.cpp",
        targets={
            "host-runtime": ("llama-server", "llama-cli", "llama-bench", "llama-perplexity"),
            "host-tools": ("llama-quantize", "llama-imatrix", "llama-perplexity", "llama-completion", "llama-bench"),
            "device-runtime": ("llama-server", "llama-cli", "llama-bench", "llama-perplexity"),
        },
        program=re.compile(r"^llama-[a-z0-9-]{1,40}$", re.ASCII), licence_name="LICENSE-llama.cpp",
        package_prefix="llama-runtime", start_script=True),
    # whisper.cpp at the pin: whisper-server (examples/server), whisper-cli and whisper-bench; only for a board
    # (the bundle's speech recognition), so only the device runtime is planned.
    WHISPER_CPP: Project(
        name=WHISPER_CPP, folder="whisper.cpp",
        targets={"device-runtime": ("whisper-server", "whisper-cli", "whisper-bench")},
        program=re.compile(r"^whisper-[a-z0-9-]{1,40}$", re.ASCII), licence_name="LICENSE-whisper.cpp",
        package_prefix="whisper-runtime", start_script=False),
}


class ProjectError(ValueError):
    """A project the self-build path does not know; the message names the known ones."""


def project(name: Optional[str]) -> Project:
    """The project of `name` (None: llama.cpp, the name every plan without one has)."""
    found = PROJECTS.get(name or LLAMA_CPP)
    if found is None:
        raise ProjectError(f"no project {name!r} (known: {', '.join(PROJECTS)})")
    return found


def project_of(thing: Any) -> Project:
    """The project of a pin (its `project`), a BuildPlan or its dict, or a StoredBuild (plan.source["project"]);
    llama.cpp when none is named."""
    name = getattr(thing, "project", None)
    if isinstance(name, str):
        return project(name)
    if not isinstance(thing, Mapping) and not hasattr(thing, "source") and hasattr(thing, "plan"):
        thing = thing.plan   # a StoredBuild: its plan as written
    source = thing.get("source") if isinstance(thing, Mapping) else getattr(thing, "source", None)
    return project(source.get("project") if isinstance(source, Mapping) else None)


def pin_of(catalog: Any, name: Optional[str] = None):
    """The pinned source of project `name` in the runtime catalog (llama_cpp.source, whisper_cpp.source); None when
    the catalog has none."""
    which = project(name).name
    if which == LLAMA_CPP:
        return catalog.llama_cpp.source
    entry = getattr(catalog, "whisper_cpp", None)
    return entry.source if entry is not None else None


__all__ = ["LLAMA_CPP", "PROJECTS", "Project", "ProjectError", "WHISPER_CPP", "pin_of", "project", "project_of"]
