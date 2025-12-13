#!/bin/bash
# source_module.sh - Environment & Tools Setup
# Part of LLM Cross-Compiler Framework
# 
# DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.
# ZWECK: Stellt sicher, dass llama.cpp geklont und die Python-Umgebung 
#        (für Konvertierung) im Docker-Container eingerichtet ist.

set -euo pipefail

# --- CONFIGURATION & GLOBALS ---
readonly SCRIPT_NAME="source_module.sh"
readonly BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
readonly REPO_DIR="${BUILD_CACHE_DIR}/repos"
# Default Pfad für llama.cpp (Symlink Ziel)
readonly LLAMA_CPP_PATH="${LLAMA_CPP_PATH:-${BUILD_CACHE_DIR}/llama.cpp}"

# Quellen aus Environment (injiziert durch Orchestrator aus project_sources.yml)
readonly LLAMA_CPP_REPO="${LLAMA_CPP_REPO_OVERRIDE:-https://github.com/ggerganov/llama.cpp.git}"
readonly LLAMA_CPP_COMMIT="${LLAMA_CPP_COMMIT:-b3626}" # Standard-Fallback (sollte via Docker Arg kommen)

readonly LOG_LEVEL="${LOG_LEVEL:-INFO}"
DEBUG="${DEBUG:-0}"

# --- LOGGING ---
log_info() { echo "ℹ️  [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_success() { echo "✅ [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_warn() { echo "⚠️  [$(date '+%H:%M:%S')] [SOURCE] $1"; }
log_error() { echo "❌ [$(date '+%H:%M:%S')] [SOURCE] $1" >&2; }

# --- ERROR HANDLING ---
cleanup_on_error() {
    log_error "Source-Modul fehlgeschlagen."
    exit 1
}
trap cleanup_on_error ERR

# --- MAIN FUNCTIONS ---

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
        local current_url=$(git remote get-url origin)
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

setup_directories() {
    log_info "Erstelle Arbeitsverzeichnisse..."
    mkdir -p "$REPO_DIR"
    mkdir -p "$BUILD_CACHE_DIR/models"
    mkdir -p "$BUILD_CACHE_DIR/output"
}

validate_python_environment() {
    log_info "Validiere Python-Umgebung (Container-nativ)..."
    
    # Im Container-Kontext (Dockerfile) sollten diese bereits installiert sein
    local required_modules=("torch" "transformers" "numpy" "sentencepiece")
    
    for module in "${required_modules[@]}"; do
        if ! python3 -c "import $module" 2>/dev/null; then
            log_error "Fehlendes Python-Modul: $module"
            log_error "Stellen Sie sicher, dass das Docker-Image korrekt gebaut wurde."
            return 1
        fi
    done
    
    log_success "Python-Umgebung validiert."
    return 0
}

# --- MAIN EXECUTION ---
main() {
    local start_time=$SECONDS
    log_info "Starte Source Module (Environment Setup)..."
    
    setup_directories
    
    # 1. Setup llama.cpp
    ensure_repo "$LLAMA_CPP_REPO" "$REPO_DIR/llama.cpp" "$LLAMA_CPP_COMMIT"
    
    # Symlink erstellen für Kompatibilität mit anderen Modulen
    # Falls $LLAMA_CPP_PATH nicht direkt auf das Repo zeigt
    if [ "$LLAMA_CPP_PATH" != "$REPO_DIR/llama.cpp" ]; then
        mkdir -p "$(dirname "$LLAMA_CPP_PATH")"
        ln -sf "$REPO_DIR/llama.cpp" "$LLAMA_CPP_PATH"
    fi
    
    # 2. Validate Env
    validate_python_environment
    
    local duration=$((SECONDS - start_time))
    log_success "Source Module abgeschlossen in ${duration}s"
}

# Nur ausführen, wenn direkt aufgerufen
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
