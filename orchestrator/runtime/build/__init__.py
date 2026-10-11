"""Self-built llama.cpp: the build recipe (a hardware profile becomes a build plan), the pinned source, and running,
verifying and storing a build.

recipe.py is pure (no I/O); toolchain.py gathers what the build machine offers (ToolchainInfo, detection);
source.py fetches, verifies and caches the pinned commit and moves it to air-gapped machines as a git bundle;
executor.py runs a plan natively or in the builder container (builder.Dockerfile, no network); verify.py checks the
result (ELF format and glibc symbol versions, --version, --list-devices, the server's refusal of --tools, the smoke
run); store.py keeps verified builds with their stamps, applies retention and imports builds made elsewhere; pins.py
checks every place that names the llama.cpp version against the one pin. host_tools.py turns the plan for the
conversion tools of a target image into its build arguments; target_runtime.py writes the runtime: block of new
target modules (the plan for the device runtime, from the target's probe).
"""

from orchestrator.runtime.build.recipe import (BuildPlan, BuildStep, PlanError, ToolchainInfo, build_id_of,
                                               check_download_options, choose_backend, choose_cuda_toolkit,
                                               plan_build, plan_variants)

__all__ = [
    "BuildPlan", "BuildStep", "PlanError", "ToolchainInfo", "build_id_of", "check_download_options",
    "choose_backend", "choose_cuda_toolkit", "plan_build", "plan_variants",
]
