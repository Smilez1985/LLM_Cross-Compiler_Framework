#!/bin/bash
# benchmark_module.sh - Performance Verification
# Part of LLM Cross-Compiler Framework
# DIREKTIVE: Goldstandard. Führt Smoke-Test und Performance-Check im Container (Host-CPU) durch.

set -euo pipefail

readonly BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
readonly LLAMA_CPP_PATH="${LLAMA_CPP_PATH:-${BUILD_CACHE_DIR}/repos/llama.cpp}"
# Nutze native Tools (da wir im x86 Container sind)
readonly BENCH_TOOL="$LLAMA_CPP_PATH/build_native/bin/llama-bench"

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
    if [[ ! -f "$BENCH_TOOL" ]]; then die "Bench tool fehlt. Wurde target_module.sh (native build) ausgeführt?"; fi

    log_info "Starte Host-Performance Benchmark (Integritätsprüfung)..."
    
    # Kurzer Testlauf
    "$BENCH_TOOL" -m "$model_path" -p 64 -n 16 -r 1
    
    echo "✅ Integritätsprüfung bestanden."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
