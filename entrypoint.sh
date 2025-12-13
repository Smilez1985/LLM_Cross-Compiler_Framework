#!/bin/bash
# entrypoint.sh - LLM Cross-Compiler Framework Container Entry Point
# DIREKTIVE: Goldstandard, robust, professionell geschrieben
# UPDATES: UID/GID Mapping für Linux-Hosts, Signal Handling

set -euo pipefail

# ============================================================================
# CONFIGURATION & GLOBALS
# ============================================================================

readonly SCRIPT_NAME="entrypoint.sh"
readonly SCRIPT_VERSION="1.1.0"

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

setup_directories() {
    # Verzeichnisse als root erstellen, damit sie existieren, bevor chown läuft
    local cache_dirs=(
        "$BUILD_CACHE_DIR/models"
        "$BUILD_CACHE_DIR/temp"
        "$BUILD_CACHE_DIR/output"
        "$BUILD_CACHE_DIR/logs"
        "$BUILD_CACHE_DIR/toolchains"
        "$BUILD_CACHE_DIR/packages"
    )
    
    for dir in "${cache_dirs[@]}"; do
        if [ ! -d "$dir" ]; then
            mkdir -p "$dir"
        fi
    done
}

fix_permissions() {
    log_info "Checking user permissions (Target: PUID=${PUID}, PGID=${PGID})..."
    
    # Aktuelle ID des llmbuilder Users holen
    local current_uid=$(id -u llmbuilder)
    local current_gid=$(id -g llmbuilder)

    # 1. User/Group Modifikation (falls notwendig)
    if [ "$PUID" != "$current_uid" ] || [ "$PGID" != "$current_gid" ]; then
        log_info "Mapping container user 'llmbuilder' to Host UID:GID ${PUID}:${PGID}"
        
        groupmod -o -g "$PGID" llmbuilder 2>/dev/null || true
        usermod -o -u "$PUID" -g "$PGID" llmbuilder 2>/dev/null || true
    else
        log_info "User IDs match internal default, no mapping needed."
    fi

    # 2. Berechtigungen setzen
    # Wir setzen Owner nur auf Verzeichnisse, die beschreibbar sein MÜSSEN.
    # Recursive chown auf /app kann langsam sein, daher Vorsicht.
    log_info "Applying permissions to storage locations..."
    
    chown -R llmbuilder:llmbuilder "$BUILD_CACHE_DIR"
    
    # Optional: /app ownership fixen, falls Code gemountet wird (Dev Mode)
    if [ -w "/app" ] || [ "$DEBUG" = "1" ]; then
         chown -R llmbuilder:llmbuilder /app
    fi
    
    log_success "Permission fix completed."
}

# ============================================================================
# CONTAINER INITIALIZATION
# ============================================================================

print_banner() {
    cat << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║                LLM Cross-Compiler Framework                  ║
║                     Rockchip Container                       ║
╠══════════════════════════════════════════════════════════════╣
║  Target: RK3566, RK3568, RK3576, RK3588                      ║
║  Modules: source → config → convert → target                 ║
║  Architecture: AArch64 Cross-Compilation                     ║
╚══════════════════════════════════════════════════════════════╝
EOF
}

initialize_container() {
    # Permission Fix VOR allem anderen ausführen (wir sind hier noch root)
    if [ "$(id -u)" = "0" ]; then
        setup_directories
        fix_permissions
        
        # Neustart des Skripts als llmbuilder User
        # Priorität: su-exec (leichtgewichtiger) -> gosu -> su
        if command -v su-exec >/dev/null; then
            exec su-exec llmbuilder "$0" "$@"
        elif command -v gosu >/dev/null; then
            exec gosu llmbuilder "$0" "$@"
        else
            log_warn "Neither su-exec nor gosu found. Falling back to standard su."
            exec su llmbuilder -c "$0 $@"
        fi
    fi

    log_info "Initializing Container Runtime"
    log_info "Identity: $(whoami) (UID: $(id -u))"
    
    verify_environment
    log_success "Ready."
}

verify_environment() {
    if ! python3 --version >/dev/null 2>&1; then
        log_error "Python3 not available"
        exit 1
    fi
    
    if [[ ! -d "$LLAMA_CPP_PATH" ]]; then
        log_error "llama.cpp source not found at: $LLAMA_CPP_PATH"
        exit 1
    fi
}

# ============================================================================
# RUNTIME MODES
# ============================================================================

run_pipeline() {
    local input_path="$1"
    local model_name="$2"
    local quant_method="${3:-Q4_K_M}"
    local hardware_config="${4:-}"
    
    log_info ">>> Starting PIPELINE for model: $model_name"
    log_info ">>> Quantization: $quant_method"
    
    export MODEL_NAME="$model_name"
    export QUANT_METHOD="$quant_method"
    export INPUT_PATH="$input_path"
    
    if [[ -n "$hardware_config" ]] && [[ -f "$hardware_config" ]]; then
        export HARDWARE_CONFIG_FILE="$hardware_config"
    fi
    
    local start_time=$SECONDS
    
    # Error Trap für die Pipeline
    trap 'log_error "Pipeline failed at step: $BASH_COMMAND"' ERR

    # Stage 1
    log_info "[1/4] Environment Setup"
    /app/modules/source_module.sh
    
    # Stage 2
    log_info "[2/4] Hardware Configuration"
    /app/modules/config_module.sh
    
    # Stage 3
    log_info "[3/4] Model Conversion"
    /app/modules/convert_module.sh --input "$input_path" --model-name "$model_name"
    
    # Stage 4
    log_info "[4/4] Quantization & Packaging"
    local gguf_file="$BUILD_CACHE_DIR/output/${model_name}.fp16.gguf"
    
    # Check if conversion actually produced the file
    if [ ! -f "$gguf_file" ]; then
        log_error "Conversion output not found: $gguf_file"
        exit 1
    fi
    
    /app/modules/target_module.sh --input "$gguf_file" --quantization "$quant_method" --model-name "$model_name"
    
    local total_time=$((SECONDS - start_time))
    log_success ">>> Pipeline COMPLETED successfully in ${total_time}s"
    
    # Trap entfernen
    trap - ERR
}

run_module() {
    local module_name="$1"
    shift
    local module_path="/app/modules/${module_name}_module.sh"
    if [[ ! -f "$module_path" ]]; then
        log_error "Module not found: $module_name"
        exit 1
    fi
    log_info "Executing Module: $module_name"
    "$module_path" "$@"
}

run_system_tests() {
    log_info "Running system integrity tests..."
    python3 -c "import torch; print('✅ PyTorch Version: ' + torch.__version__)" || exit 1
    
    if command -v aarch64-linux-gnu-gcc >/dev/null; then
        echo "✅ Cross-Compiler (GCC aarch64) detected"
    else
        log_error "Cross-Compiler missing!"
        exit 1
    fi
    log_success "All tests passed."
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    # Check for root -> Initialize & Switch User
    if [ "$(id -u)" = "0" ]; then
        initialize_container "$@"
        # initialize_container uses exec, so this code is unreachable
    fi

    # User Space Logic (llmbuilder)
    print_banner

    case "${1:-interactive}" in
        "pipeline")
            shift; run_pipeline "$@" ;;
        "source"|"config"|"convert"|"target")
            local mod="$1"; shift; run_module "$mod" "$@" ;;
        "test")
            run_system_tests ;;
        "status")
            echo "Status: Operational | User: $(whoami) | PUID: $(id -u)" ;;
        "interactive"|"bash")
            exec bash ;;
        *)
            exec "$@" ;;
    esac
}

main "$@"
