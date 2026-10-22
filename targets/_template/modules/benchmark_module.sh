#!/bin/bash
# benchmark_module.sh - Performance Verification of a GGUF file on the build host, by hand
# Part of LLM Cross-Compiler Framework
#
# Not a stage: build.sh, the stage runner, never runs it. It runs llama-bench inside the image, so it measures the
# CPU of the machine that builds, not the board. A model for a board is measured on the board:
# `llm-cli device runtime verify DEVICE --bench-model PATH` (or `device runtime deploy ... --bench-model PATH`; in the
# GUI Boards > Boards and Devices... > Verify...), whose result is recorded with the build (device_tests.json) and
# shown as "tested on <board>". Kept as a quick integrity check of a model file inside the image (Smoke-Test im
# Container, Host-CPU).

set -euo pipefail

LLAMA_CPP_PATH="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"
# The image builds llama-bench next to the conversion tools (build/bin of the pinned llama.cpp).
BENCH_TOOL="$LLAMA_CPP_PATH/build/bin/llama-bench"

log_info() { echo "ℹ️  [BENCHMARK] $1"; }
die() { echo "❌ [BENCHMARK] $1" >&2; exit 1; }

main() {
    local model_path=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --model) model_path="$2"; shift 2;;
            *) shift;;
        esac
    done

    if [[ ! -f "$model_path" ]]; then die "Model nicht gefunden: $model_path"; fi
    if [[ ! -f "$BENCH_TOOL" ]]; then die "Bench tool fehlt: $BENCH_TOOL (the image builds llama-bench)."; fi

    log_info "Starte Host-Performance Benchmark (Integritätsprüfung, Host-CPU, not the board)..."

    # Kurzer Testlauf
    "$BENCH_TOOL" -m "$model_path" -p 64 -n 16 -r 1

    echo "✅ Integritätsprüfung bestanden."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
