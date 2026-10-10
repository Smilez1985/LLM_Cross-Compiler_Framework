#!/bin/bash
# rknn_module.sh - RKNN Toolkit Execution Module
# Optimized for RK3566/RK3588 NPU (Vision/Audio/Generic)
# Part of LLM Cross-Compiler Framework

set -euo pipefail

# Environment Variables
MODEL_SOURCE="${MODEL_SOURCE:-}"
QUANTIZATION="${QUANTIZATION:-INT8}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="${OUTPUT_DIR:-${BUILD_CACHE_DIR}/output}"
SCRIPT_DIR="/app/scripts"

RKNN_TOOLKIT_DIR="/app/rknn-toolkit2"

log() { echo ">> [RKNN] [$(date '+%H:%M:%S')] $1"; }
die() { echo "❌ [RKNN] [$(date '+%H:%M:%S')] $1" >&2; exit 1; }

# --- INSTALLATION HELPER ---
ensure_rknn_installed() {
    if python3 -c "import rknn.api" &> /dev/null; then
        return 0
    fi
    
    log "Installing RKNN Toolkit2..."
    # RKNN hat separate Wheels für Python Versionen (cp38, cp310, cp311)
    # Wir müssen das richtige für die aktuelle Python-Version finden
    PY_VER=$(python3 -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')")
    
    log "Detected Python: $PY_VER"
    
    # Suche nach rknn_toolkit2-*-cp3X-*-manylinux*.whl
    WHEEL_FILE=$(find "$RKNN_TOOLKIT_DIR/packages" -name "rknn_toolkit2*${PY_VER}*x86_64.whl" | head -n 1)
    
    if [[ -z "$WHEEL_FILE" ]]; then
        # Fallback: Versuche ARM64 falls wir nativ bauen (selten im Docker container, aber möglich)
        WHEEL_FILE=$(find "$RKNN_TOOLKIT_DIR/packages" -name "rknn_toolkit2*${PY_VER}*aarch64.whl" | head -n 1)
    fi

    if [[ -z "$WHEEL_FILE" ]]; then
        die "No compatible RKNN wheel found for $PY_VER in $RKNN_TOOLKIT_DIR/packages/"
    fi
    
    log "Installing $WHEEL_FILE..."
    # FIX: Use python3 -m pip
    python3 -m pip install "$WHEEL_FILE" || die "Pip install failed."
    
    # Optional: Requirements installieren
    # python3 -m pip install -r "$RKNN_TOOLKIT_DIR/packages/requirements_$PY_VER-*.txt"
}

main() {
    log "Starting RKNN Pipeline..."

    # 1. Input Validation
    if [[ -z "$MODEL_SOURCE" ]]; then die "MODEL_SOURCE missing."; fi
    
    # 2. Quantization Mapping
    case "${QUANTIZATION:-INT8}" in
        "INT8"|"i8"|"Q8_0") Q_TYPE="i8" ;;
        "FP16"|"f16") Q_TYPE="fp16" ;;
        *) log "Defaulting to i8."; Q_TYPE="i8" ;;
    esac

    # 3. Setup & Install
    if [ ! -d "$RKNN_TOOLKIT_DIR" ]; then
        log "Cloning RKNN Toolkit2..."
        REPO="https://github.com/airockchip/rknn-toolkit2.git"
        if [ -n "${RKNN_TOOLKIT2_REPO_OVERRIDE:-}" ]; then REPO="$RKNN_TOOLKIT2_REPO_OVERRIDE"; fi
        git clone "$REPO" "$RKNN_TOOLKIT_DIR" || die "Clone failed."
    fi
    
    ensure_rknn_installed

    # 4. Execute
    CONVERTER="$SCRIPT_DIR/rknn_converter.py"
    if [ ! -f "$CONVERTER" ]; then die "Converter script missing."; fi
    
    MODEL_NAME=$(basename "$MODEL_SOURCE" .onnx)
    OUTPUT_FILE="$OUTPUT_DIR/${MODEL_NAME}_${Q_TYPE}.rknn"
    
    log "Running conversion..."
    
    set +e
    python3 "$CONVERTER" \
        --model "$MODEL_SOURCE" \
        --output "$OUTPUT_FILE" \
        --dtype "$Q_TYPE"
    
    if [ $? -eq 0 ] && [ -f "$OUTPUT_FILE" ]; then
        log "✅ Success: $OUTPUT_FILE"
    else
        die "Conversion failed."
    fi
}

main "$@"
