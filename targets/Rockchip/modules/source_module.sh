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
# LLAMA_CPP_COMMIT (a full commit id; default: the framework's pin, LLAMA_CPP_PIN_COMMIT below) from
# LLAMA_CPP_REPO_OVERRIDE (an https address without a credential; default: upstream) into $BUILD_CACHE_DIR/repos,
# checks that the checkout is that commit and points LLAMA_CPP_PATH at it: the source setup of version 1.x, for an
# image without llama.cpp. In a build, LLAMA_CPP_REPO_OVERRIDE is where the framework fetched the image's pinned
# commit from when it built the image (the build log says whether it did); nothing is fetched in a build container.

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
        log_info "LLAMA_CPP_REPO_OVERRIDE is set: the framework fetches the pinned llama.cpp commit (above) from that repository when it builds this target's image, if it is an https address without a credential (the build log says whether it did); a build container has no network, so nothing is fetched here."
    fi
}

# ============================================================================
# THE LLAMA.CPP SOURCE BY HAND (source_module.sh --clone-llama-cpp: the source setup of version 1.x)
# The same functions in targets/Rockchip and targets/_template (tests/test_target_stage.py compares them).
# ============================================================================

# The framework's llama.cpp pin (configs/llama_runtime.yml, llama_cpp.source): what --clone-llama-cpp checks out when
# LLAMA_CPP_COMMIT is not set. A derived pin place: tests/test_build_recipe.py and scripts/llama_cpp_pin.py compare it
# with the pin, so a pin bump changes it as well.
LLAMA_CPP_PIN_TAG="b11429"
LLAMA_CPP_PIN_COMMIT="d81235049384534c167caea52b85a694f6103d14"

# An https address of a git repository without a user name, password, token or query (the rule the framework's image
# builds apply to LLAMA_CPP_REPO_OVERRIDE): nothing of it reaches git as an option or a credential.
repo_url_ok() {
    [[ "$1" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%+/-]*)?$ ]]
}

ensure_repo() {
    local url="$1"
    local path="$2"
    local commit="$3"
    local current head

    log_info "Prüfe Repo: $(basename "$path") @ $commit"

    if [ ! -d "$path/.git" ]; then
        log_info "Klone Repo..."
        git clone -- "$url" "$path"
    else
        cd "$path"
        # Prüfen ob Remote URL stimmt, sonst fixen
        current="$(git remote get-url origin 2>/dev/null || true)"
        if [ "$current" != "$url" ]; then
            log_warn "URL Mismatch. Setze neu..."
            git remote set-url origin "$url"
        fi
        git fetch origin
    fi

    cd "$path"
    # Hard Reset auf den spezifischen Commit für Reproduzierbarkeit
    git checkout -f "$commit"
    git submodule update --init --recursive
    # The source is the commit asked for (as the target images check it)
    head="$(git rev-parse HEAD)"
    if [ "$head" != "$commit" ]; then
        log_error "llama.cpp: got commit $head, asked for $commit; the source is not used."
        exit 1
    fi
}

clone_llama_cpp() {
    local commit="${LLAMA_CPP_COMMIT:-$LLAMA_CPP_PIN_COMMIT}" url="${LLAMA_CPP_REPO_OVERRIDE:-$UPSTREAM_LLAMA_CPP}"
    if ! printf '%s\n' "$commit" | grep -Eqx '[0-9a-f]{40}'; then
        log_error "LLAMA_CPP_COMMIT must be a full commit id (40 hex characters), such as the pin in configs/llama_runtime.yml."
        exit 1
    fi
    if ! repo_url_ok "$url"; then
        log_error "LLAMA_CPP_REPO_OVERRIDE must be an https address of a git repository without a user name, password, token or query; nothing was cloned."
        exit 1
    fi
    if [ -z "${LLAMA_CPP_COMMIT:-}" ]; then
        log_info "LLAMA_CPP_COMMIT is not set: the framework's pin, llama.cpp $LLAMA_CPP_PIN_TAG (commit $LLAMA_CPP_PIN_COMMIT)."
    fi
    mkdir -p "$REPO_DIR"
    ensure_repo "$url" "$REPO_DIR/llama.cpp" "$commit"
    # Softlink für Pfad-Kompatibilität
    mkdir -p "$(dirname "$LLAMA_BASE")"
    ln -sfn "$REPO_DIR/llama.cpp" "$LLAMA_BASE"
    log_success "llama.cpp $commit in $REPO_DIR/llama.cpp, linked as $LLAMA_BASE."
}

# ============================================================================
# END OF LLAMA.CPP SOURCE
# ============================================================================

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
