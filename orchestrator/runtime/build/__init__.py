"""Self-built llama.cpp: the build recipe (a hardware profile becomes a build plan) and the pinned source.

recipe.py is pure (no I/O); source.py fetches, verifies and caches the pinned commit and moves it to air-gapped
machines as a git bundle; pins.py checks every place that names the llama.cpp version against the one pin. Running a
plan (the executor) is a later work package.
"""

from orchestrator.runtime.build.recipe import (BuildPlan, BuildStep, PlanError, ToolchainInfo, build_id_of,
                                               check_download_options, choose_backend, choose_cuda_toolkit,
                                               plan_build, plan_variants)

__all__ = [
    "BuildPlan", "BuildStep", "PlanError", "ToolchainInfo", "build_id_of", "check_download_options",
    "choose_backend", "choose_cuda_toolkit", "plan_build", "plan_variants",
]
