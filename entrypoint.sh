#!/bin/bash
# entrypoint.sh - LLM Cross-Compiler Framework Container Entry Point
# DIREKTIVE: Goldstandard, robust, professionell geschrieben
# UPDATES: UID/GID Mapping für Linux-Hosts

set -euo pipefail

# ============================================================================
# CONFIGURATION & GLOBALS
# ============================================================================

readonly SCRIPT_NAME="entrypoint.sh"
readonly SCRIPT_VERSION="1.1.0"
readonly FRAMEWORK_VERSION="1.0.0"

# Environment defaults
export BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
export LLAMA_CPP_PATH="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export DEBUG="${DEBUG:-0}"

# User ID Mapping Defaults (werden von docker run -e PUID=... überschrieben)
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# ============================================================================
# LOGGING
# ============================================================================

log() {
    local level="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [ENTRYPOINT] [$level] $message" >&2
}

log_info() { [[ "$LOG_LEVEL" != "ERROR" ]] && log "INFO" "$1"; }
log_warn() { log "WARN" "$1"; }
log_error() { log "ERROR" "$1"; }
log_success() { log "SUCCESS" "$1"; }

# ============================================================================
# USER PERMISSION FIX (Linux Host Support)
# ============================================================================

fix_permissions() {
    log_info "Checking user permissions (PUID=${PUID}, PGID=${PGID})..."
    
    # Aktuelle ID des llmbuilder Users holen
    local current_uid=$(id -u llmbuilder)
    local current_gid=$(id -g llmbuilder)

    if [ "$PUID" != "$current_uid" ] || [ "$PGID" != "$current_gid" ]; then
        log_info "Mapping container user 'llmbuilder' to Host UID:GID ${PUID}:${PGID}"
        
        # Gruppe anpassen
        groupmod -o -g "$PGID" llmbuilder 2>/dev/null || true
        # User anpassen
        usermod -o -u "$PUID" -g "$PGID" llmbuilder 2>/dev/null || true
        
        # Berechtigungen korrigieren für Home und Cache
        chown -R llmbuilder:llmbuilder /app
        chown -R llmbuilder:llmbuilder "$BUILD_CACHE_DIR"
        
        log_success "User mapping completed."
    else
        log_info "User IDs match, no mapping needed."
    fi
}

# ============================================================================
# CONTAINER INITIALIZATION
# ============================================================================

print_banner() {
    cat << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║               LLM Cross-Compiler Framework                   ║
║                   Rockchip Container                         ║
╠══════════════════════════════════════════════════════════════╣
║  Target: RK3566, RK3568, RK3576, RK3588                      ║
║  Modules: source → config → convert → target                 ║
║  Architecture: AArch64 Cross-Compilation                     ║
╚══════════════════════════════════════════════════════════════╝
EOF
}

initialize_container() {
    # Permission Fix VOR allem anderen ausführen (wenn wir root sind)
    if [ "$(id -u)" = "0" ]; then
        fix_permissions
        # Neustart des Skripts als llmbuilder User, um Sicherheit zu gewährleisten
        # Wir nutzen gosu wenn verfügbar, oder su-exec, oder su
        if command -v gosu >/dev/null; then
            exec gosu llmbuilder "$0" "$@"
        else
            # Fallback: su
            exec su llmbuilder -c "$0 $@"
        fi
    fi

    log_info "Initializing LLM Cross-Compiler Framework Container"
    log_info "Container User: $(whoami) (UID: $(id -u))"
    
    # Verify container environment
    verify_environment
    
    # Setup build cache permissions (als User)
    setup_build_cache
    
    log_success "Container initialization completed"
}

verify_environment() {
    # Check Python
    if ! python3 --version >/dev/null 2>&1; then
        log_error "Python3 not available"
        exit 1
    fi
    
    # Check llama.cpp
    if [[ ! -d "$LLAMA_CPP_PATH" ]]; then
        log_error "llama.cpp not found at: $LLAMA_CPP_PATH"
        exit 1
    fi
    
    log_success "Environment verification completed"
}

setup_build_cache() {
    # Create subdirectories
    local cache_dirs=(
        "$BUILD_CACHE_DIR/models"
        "$BUILD_CACHE_DIR/temp"
        "$BUILD_CACHE_DIR/output"
        "$BUILD_CACHE_DIR/logs"
        "$BUILD_CACHE_DIR/toolchains"
        "$BUILD_CACHE_DIR/packages"
    )
    
    for dir in "${cache_dirs[@]}"; do
        mkdir -p "$dir"
    done
}

# ============================================================================
# RUNTIME MODES
# ============================================================================

run_pipeline() {
    local input_path="$1"
    local model_name="$2"
    local quant_method="${3:-Q4_K_M}"
    local hardware_config="${4:-}"
    
    log_info "Starting LLM cross-compilation pipeline for $model_name"
    
    export MODEL_NAME="$model_name"
    export QUANT_METHOD="$quant_method"
    export INPUT_PATH="$input_path"
    
    if [[ -n "$hardware_config" ]] && [[ -f "$hardware_config" ]]; then
        export HARDWARE_CONFIG_FILE="$hardware_config"
    fi
    
    local start_time=$SECONDS
    
    # Execute the 4-module pipeline
    log_info "Stage 1/4: Environment Setup"
    /app/modules/source_module.sh
    
    log_info "Stage 2/4: Hardware Configuration"
    /app/modules/config_module.sh
    
    log_info "Stage 3/4: Model Conversion"
    /app/modules/convert_module.sh --input "$input_path" --model-name "$model_name"
    
    log_info "Stage 4/4: Quantization & Packaging"
    # Pfadannahme aus convert_module
    local gguf_file="$BUILD_CACHE_DIR/output/${model_name}.fp16.gguf"
    /app/modules/target_module.sh --input "$gguf_file" --quantization "$quant_method" --model-name "$model_name"
    
    local total_time=$((SECONDS - start_time))
    log_success "Pipeline completed in ${total_time} seconds"
}

run_module() {
    local module_name="$1"
    shift
    local module_path="/app/modules/${module_name}_module.sh"
    if [[ ! -f "$module_path" ]]; then
        log_error "Module not found: $module_name"
        exit 1
    fi
    "$module_path" "$@"
}

run_system_tests() {
    log_info "Running system tests..."
    python3 -c "import torch; print('✅ PyTorch OK')"
    aarch64-linux-gnu-gcc --version >/dev/null && echo "✅ Cross-Compiler OK"
    log_success "System tests completed"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    # Wenn wir root sind, initialisiere Container (Permission Fix & User Switch)
    if [ "$(id -u)" = "0" ]; then
        initialize_container "$@"
        # initialize_container führt exec aus, wir kommen hier nicht mehr hin
    fi

    # Ab hier sind wir User 'llmbuilder'
    print_banner

    case "${1:-interactive}" in
        "pipeline")
            shift; run_pipeline "$@" ;;
        "source"|"config"|"convert"|"target")
            local mod="$1"; shift; run_module "$mod" "$@" ;;
        "test")
            run_system_tests ;;
        "status")
            echo "Status: Operational (User: $(whoami))" ;;
        "interactive"|"bash")
            if [[ "${1:-}" == "bash" ]]; then exec bash; else exec bash; fi ;; # Interactive shell
        *)
            exec "$@" ;;
    esac
}

main "$@"
