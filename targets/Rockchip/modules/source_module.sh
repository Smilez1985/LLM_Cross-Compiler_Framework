#!/bin/bash
# source_module.sh - stage 1 of the Rockchip build: the toolchain sources (run by build.sh, the stage runner)
#
# Image:  targets/Rockchip/dockerfile: Debian bookworm for the build host's architecture (x86_64). Every tool in it
#         runs on the machine that builds; nothing of the image is copied to a board.
# Target: Rockchip boards, aarch64 Linux: RK3566 and RK3568 (CPU: GGUF), RK3576 and RK3588 (CPU: GGUF; NPU: RKLLM,
#         RKNN). The runtime that runs the model on the board (llama-server) is not built here: it is built from the
#         board's own hardware probe (llm-cli device runtime build).
# Stage:  names the sources the image was built from, for the build log and the resume digest of build.sh: the
#         llama.cpp build tag and commit the Dockerfile fetched and checked (the one pin: configs/llama_runtime.yml),
#         and the RKLLM and RKNN toolkit venvs. Build containers have no network, so nothing is downloaded, and this
#         stage stops no build: each later stage checks the tool it uses.
#
# By hand, in a container with network (never in a build): `source_module.sh --clone-llama-cpp` clones llama.cpp at
# LLAMA_CPP_COMMIT (a full commit id) from LLAMA_CPP_REPO_OVERRIDE (or upstream) into $BUILD_CACHE_DIR/repos and
# points LLAMA_CPP_PATH at it: the source setup of version 1.x, for an image without llama.cpp.

set -euo pipefail

BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
REPO_DIR="${BUILD_CACHE_DIR}/repos"
LLAMA_BASE="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"
UPSTREAM_LLAMA_CPP="https://github.com/ggml-org/llama.cpp.git"

# Logging
log_info() { echo "ℹ️  [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_warn() { echo "⚠️  [$(date '+%H:%M:%S')] [SOURCE] $1" >&2; }
log_success() { echo "✅ [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_error() { echo "❌ [$(date '+%H:%M:%S')] [SOURCE] $1" >&2; }

report_sources() {
    local tag commit name python
    tag="$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)"
    commit="$(cat "$LLAMA_BASE/.llama_cpp_commit" 2>/dev/null || echo unknown)"
    log_info "llama.cpp: $LLAMA_BASE (build $tag, commit $commit)"
    for name in RKLLM-Toolkit RKNN-Toolkit2; do
        if [ "$name" = "RKLLM-Toolkit" ]; then python="${RKLLM_PYTHON:-}"; else python="${RKNN_PYTHON:-}"; fi
        if [ -n "$python" ] && [ -x "$python" ]; then
            log_info "$name: $python"
        else
            log_info "$name: not in this image"
        fi
    done
    if [ -n "${LLAMA_CPP_REPO_OVERRIDE:-}" ]; then
        log_warn "LLAMA_CPP_REPO_OVERRIDE is set, but a build uses the llama.cpp of its image (above): build containers have no network."
    fi
}

ensure_repo() {
    local url="$1"
    local path="$2"
    local commit="${3:-HEAD}"

    log_info "Prüfe Repo: $(basename "$path") @ $commit"

    if [ ! -d "$path/.git" ]; then
        git clone "$url" "$path"
    else
        cd "$path"
        git fetch origin
    fi

    cd "$path"
    # Hard Reset auf den spezifischen Commit für Reproduzierbarkeit
    git checkout -f "$commit"
    git submodule update --init --recursive
}

clone_llama_cpp() {
    local commit="${LLAMA_CPP_COMMIT:-}"
    if ! printf '%s\n' "$commit" | grep -Eqx '[0-9a-f]{40}'; then
        log_error "LLAMA_CPP_COMMIT must be a full commit id (40 hex characters), such as the pin in configs/llama_runtime.yml."
        exit 1
    fi
    mkdir -p "$REPO_DIR"
    ensure_repo "${LLAMA_CPP_REPO_OVERRIDE:-$UPSTREAM_LLAMA_CPP}" "$REPO_DIR/llama.cpp" "$commit"
    # Softlink für Pfad-Kompatibilität
    mkdir -p "$(dirname "$LLAMA_BASE")"
    ln -sfn "$REPO_DIR/llama.cpp" "$LLAMA_BASE"
    log_success "llama.cpp $commit in $REPO_DIR/llama.cpp, linked as $LLAMA_BASE."
}

main() {
    report_sources
    log_success "Source Setup abgeschlossen."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [ "${1:-}" = "--clone-llama-cpp" ]; then
        clone_llama_cpp
        exit 0
    fi
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to source "$@"
    main "$@"
fi
