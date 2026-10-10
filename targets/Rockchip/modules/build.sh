#!/bin/bash
# build.sh for Rockchip (v2.4.0)
# Manually created / Template instantiated
# Handles dispatching between RK3588 (RKLLM), RK3566 (RKNN) and CPU Fallback.
# Adds support for IMatrix generation and usage.

set -euo pipefail

# --- CONFIGURATION & PATHS ---
WORK_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="${WORK_DIR}/output"
IMATRIX_DIR="${WORK_DIR}/imatrix"
mkdir -p "$OUTPUT_DIR"
mkdir -p "$IMATRIX_DIR"

TASK="${MODEL_TASK:-LLM}"
BOARD="${TARGET_BOARD:-rk3566}" 
QUANT="${QUANTIZATION:-FP16}"
JOB_TYPE="${JOB_TYPE:-build}" # 'build' or 'imatrix'

# Tools (assuming standard llama.cpp install path in container)
LLAMA_BASE="/usr/src/llama.cpp"
CONVERT_SCRIPT="$LLAMA_BASE/convert-hf-to-gguf.py"
QUANTIZE_BIN="$LLAMA_BASE/llama-quantize"
IMATRIX_BIN="$LLAMA_BASE/llama-imatrix"

echo "=== Rockchip Build Dispatcher v2.4.0 ==="
echo "Target Board: $BOARD"
echo "Task Type:    $TASK"
echo "Quantization: $QUANT"
echo "Job Type:     $JOB_TYPE"

# ==============================================================================
# 0. SPECIAL JOB: IMATRIX GENERATION
# ==============================================================================
if [[ "$JOB_TYPE" == "imatrix" ]]; then
    echo ">> [IMatrix] Starting Importance Matrix Calculation..."
    
    DATASET="${DATASET_PATH:-}"
    if [ ! -f "$DATASET" ]; then
        echo "Error: Dataset not found at $DATASET"
        exit 1
    fi

    # 1. Convert to F16 (Intermediate)
    echo ">> [IMatrix] Converting to intermediate F16 GGUF..."
    INTERMEDIATE="/tmp/model-f16.gguf"
    if [ -f "$CONVERT_SCRIPT" ]; then
        python3 "$CONVERT_SCRIPT" "$MODEL_SOURCE" --outfile "$INTERMEDIATE" --outtype f16
    else
        echo "Error: Conversion script not found."
        exit 1
    fi

    # 2. Calculate Matrix
    echo ">> [IMatrix] Calculating matrix (this may take time)..."
    OUTPUT_DAT="$IMATRIX_DIR/imatrix.dat"
    
    if [ -x "$IMATRIX_BIN" ]; then
        "$IMATRIX_BIN" -m "$INTERMEDIATE" -f "$DATASET" -o "$OUTPUT_DAT" --chunks 100
        echo ">> [IMatrix] Success. Matrix saved to $OUTPUT_DAT"
        rm -f "$INTERMEDIATE"
        exit 0
    else
        echo "Error: llama-imatrix binary not found at $IMATRIX_BIN"
        exit 1
    fi
fi

# ==============================================================================
# 1. NORMAL BUILD DISPATCH
# ==============================================================================

# Helper for GGUF Quantization
function build_gguf() {
    local q_type="$1"
    local use_matrix="${USE_IMATRIX:-0}"
    local matrix_file="${IMATRIX_PATH:-}"

    echo ">> Building GGUF (Quant: $q_type, IMatrix: $use_matrix)..."

    # Step 1: Convert to F16 first (Gold standard for quantization input)
    local f16_file="$OUTPUT_DIR/model-f16.gguf"
    if [ ! -f "$f16_file" ]; then
        echo ">> Converting HF -> GGUF F16..."
        python3 "$CONVERT_SCRIPT" "$MODEL_SOURCE" --outfile "$f16_file" --outtype f16
    fi

    # Step 2: Quantize
    local out_file="$OUTPUT_DIR/model-${q_type}.gguf"
    local quant_cmd=("$QUANTIZE_BIN" "$f16_file" "$out_file" "$q_type")

    # Apply IMatrix if requested and available
    if [[ "$use_matrix" == "1" ]] && [[ -f "$matrix_file" ]]; then
        echo ">> Applying IMatrix optimization..."
        quant_cmd+=("--imatrix" "$matrix_file")
    elif [[ "$use_matrix" == "1" ]]; then
        echo "Warning: USE_IMATRIX=1 but file '$matrix_file' not found. Fallback to standard quantization."
    fi

    echo ">> Running: ${quant_cmd[*]}"
    "${quant_cmd[@]}"

    # Cleanup F16 intermediate to save space (unless requested)
    if [[ "$q_type" != "f16" ]]; then
        rm -f "$f16_file"
    fi
}

# --- DISPATCH LOGIC ---

# 1. CPU Fallback Check (User requested FP16/Original)
if [[ "$QUANT" == "FP16" ]] || [[ "$QUANT" == *"Original"* ]]; then
    echo ">> Strategy: User requested FP16/Original. Skipping NPU quantization."
    
    # Just run F16 conversion
    if [ -f "$CONVERT_SCRIPT" ]; then
         python3 "$CONVERT_SCRIPT" "$MODEL_SOURCE" --outfile "$OUTPUT_DIR/model-f16.gguf" --outtype f16
    else
         echo "Error: llama.cpp conversion script not found."
         exit 1
    fi
    
    cd "$OUTPUT_DIR" && tar -czf "rockchip_cpu_deployment.tar.gz" *
    echo "Done."
    exit 0
fi

# 2. Hardware Specific Dispatch
if [[ "$BOARD" == *"rk3588"* ]] || [[ "$BOARD" == *"RK3588"* ]]; then
    # === RK3588 (Strong NPU) ===
    if [ "$TASK" == "LLM" ]; then
        echo ">> Strategy: RK3588 detected. Using RKLLM-Toolkit."
        if [ -f "/app/modules/rkllm_module.sh" ]; then
            /app/modules/rkllm_module.sh
        else
             echo "Error: rkllm_module.sh not found."
             exit 1
        fi
    else
        echo ">> Strategy: Non-LLM task ($TASK). Using RKNN-Toolkit2."
        if [ -f "/app/modules/rknn_module.sh" ]; then
            /app/modules/rknn_module.sh
        else
             echo "Error: rknn_module.sh not found."
             exit 1
        fi
    fi

elif [[ "$BOARD" == *"rk3566"* ]] || [[ "$BOARD" == *"RK3566"* ]]; then
    # === RK3566 (Weak NPU) ===
    if [ "$TASK" == "LLM" ]; then
        echo ">> Strategy: RK3566 detected. NPU too weak for RKLLM. Fallback to CPU (llama.cpp/GGUF)."
        
        # Map Quantization Strings
        GGUF_TYPE="q8_0" 
        if [[ "$QUANT" == "Q4"* ]]; then GGUF_TYPE="q4_k_m"; fi
        if [[ "$QUANT" == "Q5"* ]]; then GGUF_TYPE="q5_k_m"; fi
        if [[ "$QUANT" == "Q8"* ]]; then GGUF_TYPE="q8_0"; fi
        if [[ "$QUANT" == "INT4" ]]; then GGUF_TYPE="q4_k_m"; fi
        if [[ "$QUANT" == "INT8" ]]; then GGUF_TYPE="q8_0"; fi
        
        build_gguf "$GGUF_TYPE"
        
    else
        echo ">> Strategy: Voice/Vision task. Using RKNN-Toolkit2 for NPU acceleration."
        if [ -f "/app/modules/rknn_module.sh" ]; then
            /app/modules/rknn_module.sh
        else
             echo "Error: rknn_module.sh not found."
             exit 1
        fi
    fi

else
    # === GENERIC / UNKNOWN ===
    echo ">> Unknown Rockchip Board '$BOARD'. Defaulting to CPU/GGUF."
    build_gguf "q4_k_m" # Default generic quant
fi

# --- PACKAGING ---
echo "=== Packaging ==="
if [ -d "$OUTPUT_DIR" ]; then
    cd "$OUTPUT_DIR" && tar -czf "rockchip_deployment.tar.gz" *
    echo "Artifacts packaged."
else
    echo "Error: Output directory not found."
    exit 1
fi
echo "Done."
