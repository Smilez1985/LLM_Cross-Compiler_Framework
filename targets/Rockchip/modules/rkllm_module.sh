#!/bin/bash
# rkllm_module.sh - RKLLM Toolkit Execution Module (Rockchip Specific)
# Optimized for RK3588/RK3576 NPU LLM Acceleration
# Part of LLM Cross-Compiler Framework

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================

MODEL_SOURCE="${MODEL_SOURCE:-}"
QUANTIZATION="${QUANTIZATION:-w8a8}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="${OUTPUT_DIR:-${BUILD_CACHE_DIR}/output}"
SCRIPT_DIR="/app/scripts"
RKLLM_DIR="/app/rknn-llm"

log_info() { echo ">> [RKLLM-Rockchip] $(date '+%H:%M:%S') INFO: $1"; }
log_error() { echo ">> [RKLLM-Rockchip] $(date '+%H:%M:%S') ERROR: $1" >&2; }
die() { log_error "$1"; exit 1; }

# ============================================================================
# HELPER: INSTALLATION LOGIC
# ============================================================================

ensure_rkllm_installed() {
    if python3 -c "import rkllm" &> /dev/null; then
        log_info "RKLLM Python package is already installed."
        return 0
    fi

    log_info "RKLLM package missing. Attempting to install from repository..."
    
    # Suche nach dem Wheel im geklonten Repo
    # Pfad-Struktur ist meist: rknn-llm/rkllm-toolkit/packages/rkllm_toolkit-*.whl
    # Wir suchen rekursiv, um sicherzugehen
    WHEEL_FILE=$(find "$RKLLM_DIR" -name "rkllm_toolkit*.whl" | head -n 1)

    if [[ -z "$WHEEL_FILE" ]]; then
        die "No RKLLM wheel found in $RKLLM_DIR. Cannot install SDK."
    fi

    log_info "Found wheel: $WHEEL_FILE"
    log_info "Installing via pip..."

    # FIX: Use python3 -m pip for consistency
    if python3 -m pip install "$WHEEL_FILE"; then
        log_info "Installation successful."
    else
        die "Failed to install RKLLM wheel."
    fi
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    log_info "Starting RKLLM Pipeline for Rockchip Target..."

    # 1. Input Check
    if [[ -z "$MODEL_SOURCE" || ! -d "$MODEL_SOURCE" ]]; then
        die "Invalid MODEL_SOURCE: '$MODEL_SOURCE'. Must be a directory (HuggingFace format)."
    fi

    # 2. Quantization Logic
    case "${QUANTIZATION}" in
        "w8a8"|"W8A8"|"INT8"|"Q8_0") Q_TYPE="w8a8" ;;
        "w4a16"|"W4A16"|"INT4"|"Q4_K_M") Q_TYPE="w4a16" ;;
        *) log_info "Fallback to w8a8 quantization."; Q_TYPE="w8a8" ;;
    esac

    TARGET_PLATFORM="rk3588"
    if [[ "${TARGET_BOARD:-}" == *"3576"* ]]; then TARGET_PLATFORM="rk3576"; fi

    # 3. Toolkit Setup (Clone)
    if [ ! -d "$RKLLM_DIR" ]; then
        log_info "Cloning RKLLM Toolkit..."
        REPO_URL="${RKLLM_TOOLKIT_REPO_OVERRIDE:-https://github.com/airockchip/rknn-llm.git}"
        git clone "$REPO_URL" "$RKLLM_DIR" || die "Clone failed."
    fi

    # 4. Toolkit Installation (Install Wheel)
    ensure_rkllm_installed

    # 5. Run Python Exporter
    CONVERTER="$SCRIPT_DIR/export_rkllm.py"
    if [ ! -f "$CONVERTER" ]; then die "Script $CONVERTER missing."; fi
    
    MODEL_NAME=$(basename "$MODEL_SOURCE")
    OUTPUT_FILE="$OUTPUT_DIR/${MODEL_NAME}_${TARGET_PLATFORM}_${Q_TYPE}.rkllm"

    log_info "Converting $MODEL_NAME to $Q_TYPE..."
    
    set +e
    python3 "$CONVERTER" \
        --model "$MODEL_SOURCE" \
        --output "$OUTPUT_FILE" \
        --quant "$Q_TYPE" \
        --target "$TARGET_PLATFORM"
    
    if [ $? -eq 0 ] && [ -f "$OUTPUT_FILE" ]; then
        log_info "✅ Artifact Created: $OUTPUT_FILE"
    else
        die "Conversion failed."
    fi
}

main "$@"
