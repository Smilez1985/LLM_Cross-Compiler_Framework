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

# The su fallback below resets PATH; keep the image's Python venv first so python3/pip resolve to it.
if [ -n "${VIRTUAL_ENV:-}" ] && [ -d "$VIRTUAL_ENV/bin" ]; then
    case ":$PATH:" in
        *":$VIRTUAL_ENV/bin:"*) ;;
        *) export PATH="$VIRTUAL_ENV/bin:$PATH" ;;
    esac
fi

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
    
    # Read-only bind mounts (dataset, imatrix) cannot be chowned; that must not abort the container.
    chown -R llmbuilder:llmbuilder "$BUILD_CACHE_DIR" 2>/dev/null \
        || log_warn "Some files in $BUILD_CACHE_DIR were not chowned (read-only mounts are expected)."
    
    # Optional: /app ownership fixen, falls Code gemountet wird (Dev Mode)
    if [ -w "/app" ] || [ "$DEBUG" = "1" ]; then
         chown -R llmbuilder:llmbuilder /app 2>/dev/null \
             || log_warn "Some files in /app were not chowned (read-only mounts are expected)."
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
        # With PUID=0 llmbuilder is mapped to uid 0, so the restarted script is root again;
        # the marker stops it from initializing (and re-executing) forever.
        export LLM_ENTRYPOINT_INITIALIZED=1
        if command -v su-exec >/dev/null; then
            exec su-exec llmbuilder "$0" "$@"
        elif command -v gosu >/dev/null; then
            exec gosu llmbuilder "$0" "$@"
        else
            log_warn "Neither su-exec nor gosu found. Falling back to standard su."
            # Arguments are passed separately; interpolating them into the -c string would split and execute them.
            exec su llmbuilder -s /bin/bash -c 'exec "$0" "$@"' -- "$0" "$@"
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

# Both modes run the canonical build structure (DECISIONS U9): /app/modules/build.sh, the stage runner, runs the
# stages source -> config -> convert -> target of the target's modules; a stage that finished before with the same
# inputs is resumed, not run again (markers in $BUILD_SCRATCH_DIR/stages, see build.sh).
readonly STAGE_RUNNER_SCRIPT="${STAGE_RUNNER_SCRIPT:-/app/modules/build.sh}"

run_pipeline() {
    local input_path="${1:-}"
    local model_name="${2:-}"
    local quant_method="${3:-Q4_K_M}"
    local hardware_config="${4:-}"

    if [[ -z "$input_path" ]] || [[ -z "$model_name" ]]; then
        log_error "Usage: pipeline INPUT_PATH MODEL_NAME [QUANTIZATION] [HARDWARE_CONFIG]"
        exit 2
    fi

    log_info ">>> Starting PIPELINE for model: $model_name"
    log_info ">>> Quantization: $quant_method"

    # Stage 4/4 of version 1.x: "Quantization & Packaging" (the package for the board, also without a profile)
    local args=(--input "$input_path" --model-name "$model_name" --quantization "$quant_method" --package)
    if [[ -n "$hardware_config" ]] && [[ -f "$hardware_config" ]]; then
        args+=(--hardware-config "$hardware_config")
    fi

    local start_time=$SECONDS
    local rc=0
    bash "$STAGE_RUNNER_SCRIPT" "${args[@]}" || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        log_error "Pipeline failed (exit $rc); the stage that failed is named above."
        exit "$rc"
    fi

    local total_time=$((SECONDS - start_time))
    log_success ">>> Pipeline COMPLETED successfully in ${total_time}s"
}

# One stage: the build up to that stage (the stages before it resume when they finished before). The options of the
# 1.x modules (--input, --model-name, --quantization, --hardware-config) are passed to the stage runner.
run_module() {
    local module_name="$1"
    shift
    local module_path
    module_path="$(dirname "$STAGE_RUNNER_SCRIPT")/${module_name}_module.sh"
    if [[ ! -f "$module_path" ]]; then
        log_error "Module not found: $module_name"
        exit 1
    fi
    if [[ "$module_name" == "source" ]] && [[ "${1:-}" == "--clone-llama-cpp" ]]; then
        # The source setup of version 1.x (clone llama.cpp, needs network): the module itself, never a build stage
        log_info "Executing Module: source --clone-llama-cpp"
        exec bash "$module_path" "$@"
    fi
    log_info "Executing Module: $module_name (the build up to this stage)"
    if [[ "$module_name" == "target" ]]; then
        # target_module.sh of version 1.x quantized and packaged: the package for the board
        exec bash "$STAGE_RUNNER_SCRIPT" --to target --package "$@"
    fi
    exec bash "$STAGE_RUNNER_SCRIPT" --to "$module_name" "$@"
}

# PYTHON: the interpreter of an SDK venv (VENV/bin/python). True, after saying why, when VENV/NOT_INSTALLED gives the
# reason the image has no such venv (an image built on arm64 has no RKLLM-Toolkit, whose wheels are x86_64 only).
sdk_not_installed() {
    local reason
    reason="$(dirname "$(dirname "$1")")/NOT_INSTALLED"
    [ -f "$reason" ] || return 1
    echo "ℹ️  Not in this image: $(cat "$reason")"
}

run_system_tests() {
    log_info "Running system integrity tests..."
    python3 -c "import torch, gguf; print('✅ GGUF toolchain: torch ' + torch.__version__)" || exit 1

    local llama="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"
    for tool in llama-quantize llama-imatrix; do
        if [ -x "$llama/build/bin/$tool" ]; then
            echo "✅ $tool present"
        else
            log_error "$tool missing in $llama/build/bin"
            exit 1
        fi
    done
    [ -f "$llama/convert_hf_to_gguf.py" ] || { log_error "convert_hf_to_gguf.py missing"; exit 1; }

    # Optional SDK venvs (only targets that ship them). A venv the image cannot have on the computer it was built for
    # (RKLLM-Toolkit has x86_64 wheels only) is a folder with NOT_INSTALLED, the reason: reported, not tested.
    if [ -n "${RKLLM_PYTHON:-}" ] && ! sdk_not_installed "$RKLLM_PYTHON"; then
        "$RKLLM_PYTHON" -c "from rkllm.api import RKLLM; RKLLM()" >/dev/null && echo "✅ RKLLM toolkit" || { log_error "RKLLM toolkit broken"; exit 1; }
    fi
    if [ -n "${RKNN_PYTHON:-}" ] && ! sdk_not_installed "$RKNN_PYTHON"; then
        "$RKNN_PYTHON" -c "from rknn.api import RKNN; RKNN(verbose=False)" >/dev/null && echo "✅ RKNN toolkit" || { log_error "RKNN toolkit broken"; exit 1; }
    fi

    # Each toolchain venv must be internally consistent (the SDKs pin conflicting torch versions).
    local py
    for py in python3 ${RKLLM_PYTHON:-} ${RKNN_PYTHON:-}; do
        if [ "$py" != python3 ] && sdk_not_installed "$py" >/dev/null; then
            continue
        fi
        "$py" -m pip check >/dev/null || { log_error "Broken requirements in $("$py" -c 'import sys; print(sys.prefix)')"; "$py" -m pip check; exit 1; }
    done
    echo "✅ Python environments consistent (pip check)"

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
    if [ "$(id -u)" = "0" ] && [ -z "${LLM_ENTRYPOINT_INITIALIZED:-}" ]; then
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
