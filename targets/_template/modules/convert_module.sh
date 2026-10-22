#!/bin/bash
# convert_module.sh - stage 3 of the build of [MODULE_NAME]: the model conversion (run by build.sh, the stage runner)
# Part of LLM Cross-Compiler Framework
#
# Image:  this target's Dockerfile (base image debian:bookworm-slim) for the build host's architecture; llama.cpp's
#         converter and tools below LLAMA_CPP_PATH (/usr/src/llama.cpp), built by the Dockerfile from the pin.
# Target: [MODULE_NAME] ([IHRE_ARCHITEKTUR] Linux).
# Stage:  converts the model for the requested type. The quantization block of the module definition becomes
#         branches of the `case "$QUANT_TYPE"` below (after the SDK setup of the source stage). A branch may call
#         `build_gguf <TYPE>` or `convert_to_gguf <outtype> <file>`; every other type goes through build_gguf, which
#         refuses types llama-quantize does not know. build_gguf writes the types the converter writes itself (F16,
#         BF16, F32, Q8_0) straight into the output folder; for every other type it writes an F16 intermediate into
#         the work folder and leaves the quantization to the target stage (after the rotate hook, when the target has
#         one). Nothing is ever copied through unchanged.
# Job imatrix: the importance matrix of a GGUF build (F16 intermediate, then llama-imatrix over 100 chunks), which the
# BuildEngine hands to the build job (USE_IMATRIX, IMATRIX_PATH).

set -euo pipefail

MODULES_DIR="${MODULES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# The functions of the stages before: the SDK environment (source_module.sh), the GGUF types and the plan paths
# (config_module.sh); their main runs only in their own stage.
# shellcheck source=source_module.sh
source "$MODULES_DIR/source_module.sh"
# shellcheck source=config_module.sh
source "$MODULES_DIR/config_module.sh"
QUANTIZE_JOBS="$PLAN_DIR/quantize_jobs.sh"

# --- VALIDATION LOGIC ----------------------------------------------------------

# The model folder holds what the converter needs (Security Fix from v1.7.1: tokenizer files, so a build does not
# fail late). Only warnings: the converter has the last word, and its own message follows when it fails.
validate_huggingface_model() {
    local model_path="$1" shards
    [ -d "$model_path" ] || return 0
    if [[ ! -f "$model_path/config.json" ]]; then
        echo "WARNING: Missing required file: config.json in the model folder; the conversion will most likely fail." >&2
    fi
    if [[ ! -f "$model_path/tokenizer.json" ]] && [[ ! -f "$model_path/tokenizer.model" ]] \
            && [[ ! -f "$model_path/vocab.json" ]]; then
        echo "WARNING: Missing Tokenizer files (tokenizer.json, tokenizer.model or vocab.json) in the model folder." >&2
    fi
    shards="$(find "$model_path" -maxdepth 1 \( -name "model-*.safetensors" -o -name "pytorch_model-*.bin" \) \
        2>/dev/null || true)"
    if [[ ! -f "$model_path/model.safetensors" ]] && [[ ! -f "$model_path/pytorch_model.bin" ]] && [[ -z "$shards" ]]; then
        echo "WARNING: No model weights found (model.safetensors, pytorch_model.bin or their shards) in the model folder." >&2
    fi
}

# --- GGUF helpers ------------------------------------------------------------

convert_to_gguf() {
    local outtype="$1" outfile="$2"
    [ -f "$CONVERT_SCRIPT" ] || die "llama.cpp converter not found at $CONVERT_SCRIPT (the image must build llama.cpp)."
    echo ">> Converting HF -> GGUF ($outtype)..."
    python3 "$CONVERT_SCRIPT" "$MODEL_SOURCE" --outfile "$outfile" --outtype "$outtype"
    stage_output "$outfile"
}

# The model for one GGUF type: written directly by the converter, or an F16 intermediate that the target stage
# quantizes (quantize_jobs.sh lists what it has to do).
build_gguf() {
    local q q_lower f16_file out_file
    q="$(normalize_gguf_type "${1:-$QUANT_TYPE}")"
    q_lower="$(echo "$q" | tr '[:upper:]' '[:lower:]')"

    if [[ "$GGUF_CONVERT_TYPES" == *" $q_lower "* ]]; then
        convert_to_gguf "$q_lower" "$OUTPUT_DIR/${MODEL_NAME}-${q}.gguf"
        return
    fi
    [[ "$GGUF_QUANT_TYPES" == *" $q "* ]] || die "Unsupported GGUF quantization '$q'. Supported: F16 BF16 F32 Q8_0 and$GGUF_QUANT_TYPES"
    [ -x "$QUANTIZE_BIN" ] || die "llama-quantize not found at $QUANTIZE_BIN (the image must build llama.cpp)."

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
    printf 'quantize_gguf %q %q %q' "$q" "$f16_file" "$out_file" >> "$QUANTIZE_JOBS"
    if [ ${#imatrix_args[@]} -gt 0 ]; then
        printf ' %q' "${imatrix_args[@]}" >> "$QUANTIZE_JOBS"
    fi
    echo >> "$QUANTIZE_JOBS"
}

build_imatrix() {
    local dataset="${DATASET_PATH:-}" f16_file
    [ -f "$dataset" ] || die "Calibration dataset not found at '$dataset'."
    [ -x "$IMATRIX_BIN" ] || die "llama-imatrix not found at $IMATRIX_BIN (the image must build llama.cpp)."
    mkdir -p "$IMATRIX_DIR"
    f16_file="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
    convert_to_gguf f16 "$f16_file"
    echo ">> Computing importance matrix..."
    # -o *.dat keeps the legacy format, which llama-quantize --imatrix accepts.
    "$IMATRIX_BIN" -m "$f16_file" -f "$dataset" -o "$IMATRIX_DIR/imatrix.dat" --chunks 100
    rm -f "$f16_file"
    stage_output "$IMATRIX_DIR/imatrix.dat"
}

main() {
    # shellcheck source=/dev/null
    source "$BUILD_PLAN"

    if [[ "$JOB" == "imatrix" ]]; then
        build_imatrix
        return
    fi

    load_sdk_environment
    validate_huggingface_model "$MODEL_SOURCE"
    : > "$QUANTIZE_JOBS"

    # --- CONVERSION & QUANTIZATION ---
    case "$QUANT_TYPE" in
[QUANTIZATION_LOGIC]

        *)
            build_gguf "$QUANT_TYPE"
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
