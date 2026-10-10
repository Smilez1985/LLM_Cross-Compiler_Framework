#!/bin/bash
# source_module.sh - Environment & Tools Setup
# DIREKTIVE: Goldstandard, Reproduzierbar (Pinned Commits).

set -euo pipefail
readonly BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
readonly REPO_DIR="${BUILD_CACHE_DIR}/repos"

# Quellen aus Environment (injiziert durch Orchestrator aus project_sources.yml)
readonly LLAMA_CPP_REPO="${LLAMA_CPP_REPO_OVERRIDE:-https://github.com/ggerganov/llama.cpp.git}"
readonly LLAMA_CPP_COMMIT="${LLAMA_CPP_COMMIT:-b3626}" 

# Logging
log_info() { echo "ℹ️  [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_success() { echo "✅ [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_error() { echo "❌ [$(date '+%H:%M:%S')] [SOURCE] $1" >&2; }

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

main() {
    mkdir -p "$REPO_DIR"
    
    # LLAMA CPP (Pinned via Environment/Config)
    ensure_repo "$LLAMA_CPP_REPO" "$REPO_DIR/llama.cpp" "$LLAMA_CPP_COMMIT"
    
    # Softlink für Pfad-Kompatibilität
    mkdir -p "$(dirname "$LLAMA_CPP_PATH")"
    ln -sf "$REPO_DIR/llama.cpp" "$LLAMA_CPP_PATH"
    
    log_success "Source Setup abgeschlossen."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
