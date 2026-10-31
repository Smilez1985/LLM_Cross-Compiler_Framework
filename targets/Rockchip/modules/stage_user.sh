#!/bin/bash
# stage_user.sh - DEPRECATED: the stage account's helper (run_as_stage, stage_preflight, stage_share) is the
# framework's own now, in the runtime layer (orchestrator/runtime_layer/stage_user.sh, mounted read-only at /llmcf in
# every build container). This file only sources that copy, for a script started by hand that still sources
# modules/stage_user.sh; it holds no logic of its own and will be removed in a later version. A target cannot ship the
# helper: a build never reads this file.

_llmcf_layer="${LLMCF_LAYER:-/llmcf}"
[ -f "$_llmcf_layer/stage_user.sh" ] || _llmcf_layer="$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator/runtime_layer"
# shellcheck source=../../../orchestrator/runtime_layer/stage_user.sh
. "$_llmcf_layer/stage_user.sh"
unset _llmcf_layer
