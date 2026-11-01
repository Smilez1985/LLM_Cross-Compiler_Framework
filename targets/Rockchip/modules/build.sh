#!/bin/bash
# build.sh - DEPRECATED: the stage runner is the framework's own now (the runtime layer's runner.sh). This file only
# hands over to it, so a start by hand that still names build.sh (an older note, entrypoint.sh's old default) keeps
# working; it holds no logic of its own and will be removed in a later version.
#
# A build no longer runs a target's build.sh at all: the host mounts the framework's runtime layer read-only at /llmcf
# into every build container, sets the container's entrypoint to it and runs this target's stage modules in the order
# of its target.yml (stages:, orchestrator/Core/runtime_layer.py stage_order) as the stage account without any
# capability. A build.sh that a pack or an own target ships is ignored with a plain note. What used to be described
# here (the four stages, resume markers, progress lines, the end record in the runner's own end folder, the loop
# protection) is in the header of orchestrator/runtime_layer/runner.sh.
#
# Usage: the options of runner.sh (runner.sh --help).

layer="${LLMCF_LAYER:-/llmcf}"
[ -f "$layer/runner.sh" ] || layer="$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator/runtime_layer"
LLMCF_MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" exec sh "$layer/runner.sh" "$@"
