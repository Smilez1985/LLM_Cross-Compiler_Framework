#!/bin/bash
# convert_module.sh - stage 3 of the Rockchip build: the model conversion (run by build.sh, the stage runner)
# Part of LLM Cross-Compiler Framework
#
# Image:  targets/Rockchip/dockerfile: Debian bookworm for the build host's architecture (x86_64); the converters run
#         there, in the venvs of the image (llama.cpp: /opt/venv, RKLLM-Toolkit: /opt/venv-rkllm, RKNN-Toolkit2:
#         /opt/venv-rknn).
# Target: Rockchip boards, aarch64 Linux (RK3566, RK3568: CPU; RK3576, RK3588: CPU and NPU).
# Stage:  converts the model, by the format of the build plan (config_module.sh):
#   gguf   llama.cpp's convert_hf_to_gguf.py. The types it writes itself (F16, BF16, F32, Q8_0) go straight into the
#          output folder; for every other type it writes an F16 intermediate into the work folder and leaves the
#          quantization to the target stage (after the rotate hook, when the target has one).
#   rkllm  the format helper rkllm_module.sh (RKLLM-Toolkit, NPU of the RK3588 and RK3576): the finished .rkllm, which
#          the toolkit quantizes itself
#   rknn   the format helper rknn_module.sh (RKNN-Toolkit2, ONNX models): the finished .rknn
# Job imatrix: the importance matrix of a GGUF build (F16 intermediate, then llama-imatrix over 100 chunks), which the
# BuildEngine hands to the build job (USE_IMATRIX, IMATRIX_PATH).

set -euo pipefail

MODULES_DIR="${MODULES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# The GGUF types and their names come from the stage before (config_module.sh defines, its main runs only there).
# shellcheck source=config_module.sh
source "$MODULES_DIR/config_module.sh"
QUANTIZE_JOBS="$PLAN_DIR/quantize_jobs.sh"

log_warn() { echo "WARNING: $*" >&2; }

# ============================================================================
# VALIDATION LOGIC
# ============================================================================

# The model folder holds what the converters need (v1.7.1: tokenizer files, so a build does not fail late). Only
# warnings: the converter has the last word, and its own message follows when it fails.
validate_huggingface_model() {
    local model_path="$1"
    [ -d "$model_path" ] || return 0

    # 1. Config Check
    if [[ ! -f "$model_path/config.json" ]]; then
        log_warn "Missing required file: config.json in the model folder; the conversion will most likely fail."
    fi

    # 2. Tokenizer Check: tokenizer.json OR tokenizer.model (older models: vocab.json)
    if [[ ! -f "$model_path/tokenizer.json" ]] && [[ ! -f "$model_path/tokenizer.model" ]] \
            && [[ ! -f "$model_path/vocab.json" ]]; then
        log_warn "Missing Tokenizer files (tokenizer.json, tokenizer.model or vocab.json) in the model folder."
    fi

    # 3. Weight Check (single file or shards)
    local shards
    shards="$(find "$model_path" -maxdepth 1 \( -name "model-*.safetensors" -o -name "pytorch_model-*.bin" \) \
        2>/dev/null || true)"
    if [[ ! -f "$model_path/model.safetensors" ]] && [[ ! -f "$model_path/pytorch_model.bin" ]] && [[ -z "$shards" ]]; then
        log_warn "No model weights found (model.safetensors, pytorch_model.bin or their shards) in the model folder."
    fi
}

# ============================================================================
# GGUF
# ============================================================================

convert_to_gguf() {
    local outtype="$1" outfile="$2"
    [ -f "$CONVERT_SCRIPT" ] || die "llama.cpp converter not found at $CONVERT_SCRIPT"
    echo ">> Converting HF -> GGUF ($outtype)..."
    python3 "$CONVERT_SCRIPT" "$MODEL_SOURCE" --outfile "$outfile" --outtype "$outtype"
}

# The model for one GGUF type: written directly by the converter, or an F16 intermediate that the target stage
# quantizes (quantize_jobs.sh lists what it has to do).
build_gguf() {
    local q f16_file out_file
    q="$(normalize_gguf_type "${1:-$QUANT}")"
    local q_lower
    q_lower="$(echo "$q" | tr '[:upper:]' '[:lower:]')"

    if [[ "$GGUF_CONVERT_TYPES" == *" $q_lower "* ]]; then
        out_file="$OUTPUT_DIR/${MODEL_NAME}-${q}.gguf"
        convert_to_gguf "$q_lower" "$out_file"
        stage_output "$out_file"
        return
    fi
    [[ "$GGUF_QUANT_TYPES" == *" $q "* ]] || die "Unsupported GGUF quantization '$QUANT'. Supported: F16 BF16 F32 Q8_0 and$GGUF_QUANT_TYPES"
    [ -x "$QUANTIZE_BIN" ] || die "llama-quantize not found at $QUANTIZE_BIN"

    local imatrix_args=()
    if [[ "${USE_IMATRIX:-0}" == "1" ]]; then
        [ -f "${IMATRIX_PATH:-}" ] || die "USE_IMATRIX=1 but importance matrix '${IMATRIX_PATH:-}' is missing."
        imatrix_args=(--imatrix "$IMATRIX_PATH")
    elif [[ "$GGUF_NEEDS_IMATRIX" == *" $q "* ]]; then
        die "$q needs an importance matrix; enable IMatrix calibration for this build."
    elif [[ "$GGUF_WANTS_IMATRIX" == *" $q "* ]]; then
        echo "WARNING: $q without an importance matrix loses quality; enable IMatrix calibration for better results." >&2
    fi

    f16_file="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
    out_file="$OUTPUT_DIR/${MODEL_NAME}-${q}.gguf"
    convert_to_gguf f16 "$f16_file"
    stage_output "$f16_file"
    printf 'quantize_gguf %q %q %q' "$q" "$f16_file" "$out_file" >> "$QUANTIZE_JOBS"
    if [ ${#imatrix_args[@]} -gt 0 ]; then
        printf ' %q' "${imatrix_args[@]}" >> "$QUANTIZE_JOBS"
    fi
    echo >> "$QUANTIZE_JOBS"
}

build_imatrix() {
    local dataset="${DATASET_PATH:-}" f16_file
    [ -f "$dataset" ] || die "Calibration dataset not found at '$dataset'."
    [ -x "$IMATRIX_BIN" ] || die "llama-imatrix not found at $IMATRIX_BIN"
    mkdir -p "$IMATRIX_DIR"
    f16_file="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
    convert_to_gguf f16 "$f16_file"
    echo ">> Computing importance matrix..."
    # -o *.dat keeps the legacy format, which llama-quantize --imatrix accepts.
    "$IMATRIX_BIN" -m "$f16_file" -f "$dataset" -o "$IMATRIX_DIR/imatrix.dat" --chunks 100
    rm -f "$f16_file"
    stage_output "$IMATRIX_DIR/imatrix.dat"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    # shellcheck source=/dev/null
    source "$BUILD_PLAN"

    if [[ "$JOB" == "imatrix" ]]; then
        build_imatrix
        return
    fi

    : > "$QUANTIZE_JOBS"
    case "$FORMAT" in
        gguf)
            validate_huggingface_model "$MODEL_SOURCE"
            build_gguf
            ;;
        rkllm)
            validate_huggingface_model "$MODEL_SOURCE"
            bash "$MODULES_DIR/rkllm_module.sh"
            ;;
        rknn)
            bash "$MODULES_DIR/rknn_module.sh"
            ;;
        *)
            die "Format '$FORMAT' is not supported by the Rockchip target (gguf, rkllm, rknn)."
            ;;
    esac
    stage_output "$QUANTIZE_JOBS"
    local f
    for f in "$OUTPUT_DIR"/*; do
        if [ -f "$f" ]; then
            stage_output "$f"
        fi
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to convert "$@"
    main "$@"
fi
