#!/bin/bash
# source_module.sh - stage 1 of the build of [MODULE_NAME]: toolchain sources and SDK setup (run by build.sh, the
# stage runner)
# Part of LLM Cross-Compiler Framework
#
# Image:  this target's Dockerfile (base image debian:bookworm-slim) for the build host's architecture. Every tool in it
#         runs on the machine that builds; nothing of the image is copied to a board.
# Target: [MODULE_NAME] ([IHRE_ARCHITEKTUR] Linux). The runtime that runs the model on the board (llama-server) is not
#         built here: it is built from the board's own hardware probe (llm-cli device runtime build).
# Stage:  names the sources the image was built from (the llama.cpp build tag and commit its Dockerfile checked; the
#         one pin: configs/llama_runtime.yml), says when the converter's Python packages are missing (warnings
#         only), and runs the SDK setup commands of this target once. What they export (PATH, LD_LIBRARY_PATH, the
#         variables of a vendor setup script) is recorded in sdk_environment.sh, which the convert and target
#         stages load: every stage is its own process, and a resumed build keeps the setup of its first run. Export
#         every variable the later stages need. Build containers have no network, so nothing is downloaded here.
#
# By hand, in a container with network (never in a build): `source_module.sh --clone-llama-cpp` clones llama.cpp at
# LLAMA_CPP_COMMIT (a full commit id) from LLAMA_CPP_REPO_OVERRIDE (or upstream) into $BUILD_CACHE_DIR/repos and
# points LLAMA_CPP_PATH at it: the source setup of version 1.x, for an image without llama.cpp.

set -euo pipefail

BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
REPO_DIR="${BUILD_CACHE_DIR}/repos"
LLAMA_BASE="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"
UPSTREAM_LLAMA_CPP="https://github.com/ggml-org/llama.cpp.git"
SDK_ENVIRONMENT="${STAGE_DIR:-${BUILD_SCRATCH_DIR:-$BUILD_CACHE_DIR}}/sdk_environment.sh"

# --- LOGGING ---
log_info() { echo "ℹ️  [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_success() { echo "✅ [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_warn() { echo "⚠️  [$(date '+%H:%M:%S')] [SOURCE] $1" >&2; }
log_error() { echo "❌ [$(date '+%H:%M:%S')] [SOURCE] $1" >&2; }

# --- MAIN FUNCTIONS ---

report_sources() {
    local tag commit
    tag="$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)"
    commit="$(cat "$LLAMA_BASE/.llama_cpp_commit" 2>/dev/null || echo unknown)"
    log_info "llama.cpp: $LLAMA_BASE (build $tag, commit $commit)"
    if [ -n "${LLAMA_CPP_REPO_OVERRIDE:-}" ]; then
        log_warn "LLAMA_CPP_REPO_OVERRIDE is set, but a build uses the llama.cpp of its image (above): build containers have no network."
    fi
}

validate_python_environment() {
    log_info "Validiere Python-Umgebung (Container-nativ)..."

    # Im Container-Kontext (Dockerfile) sollten diese bereits installiert sein; looked up, never imported.
    local required_modules=("torch" "transformers" "numpy" "sentencepiece")
    local module
    for module in "${required_modules[@]}"; do
        if ! python3 -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)" 2>/dev/null; then
            log_warn "Fehlendes Python-Modul: $module (convert_hf_to_gguf.py needs it; the Dockerfile installs it)."
        fi
    done
}

# The SDK setup commands of this target (module wizard / Ditto): no network, unprivileged.
sdk_setup() {
    # --- SDK SETUP ---
    # [SDK_SETUP_COMMANDS]
    :
}

record_sdk_environment() {
    local before="$SDK_ENVIRONMENT.before"
    export -p > "$before"
    sdk_setup
    { export -p | grep -vxF -f "$before" || true; } | sed 's/^declare -x /export /' > "$SDK_ENVIRONMENT"
    rm -f "$before"
    stage_output "$SDK_ENVIRONMENT"
}

# The convert and target stages: what the setup commands exported.
load_sdk_environment() {
    if [ -f "$SDK_ENVIRONMENT" ]; then
        # shellcheck source=/dev/null
        source "$SDK_ENVIRONMENT"
    fi
}

ensure_repo() {
    local url="$1"
    local path="$2"
    local commit="${3:-HEAD}"

    log_info "Prüfe Repo: $(basename "$path") @ $commit"

    if [ ! -d "$path/.git" ]; then
        log_info "Klone Repo..."
        git clone "$url" "$path"
    else
        cd "$path"
        # Prüfen ob Remote URL stimmt, sonst fixen
        local current_url
        current_url=$(git remote get-url origin)
        if [ "$current_url" != "$url" ]; then
             log_warn "URL Mismatch. Setze neu..."
             git remote set-url origin "$url"
        fi
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
    # Symlink erstellen für Kompatibilität mit anderen Modulen
    mkdir -p "$(dirname "$LLAMA_BASE")"
    ln -sfn "$REPO_DIR/llama.cpp" "$LLAMA_BASE"
    log_success "llama.cpp $commit in $REPO_DIR/llama.cpp, linked as $LLAMA_BASE."
}

# --- MAIN EXECUTION ---
main() {
    local start_time=$SECONDS
    log_info "Starte Source Module (Environment Setup)..."
    report_sources
    validate_python_environment
    # The importance-matrix job runs the converter only, without the SDK (as before the stage runner).
    if [ "${JOB_TYPE:-build}" != "imatrix" ]; then
        record_sdk_environment
    fi
    log_success "Source Module abgeschlossen in $((SECONDS - start_time))s"
}

# Nur ausführen, wenn direkt aufgerufen
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [ "${1:-}" = "--clone-llama-cpp" ]; then
        clone_llama_cpp
        exit 0
    fi
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to source "$@"
    main "$@"
fi
