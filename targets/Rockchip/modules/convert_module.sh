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
#          quantization to the target stage (after the rotate hook, when the target has one). With --output (1.x:
#          convert_module.sh --input DIR --output FILE --model-name NAME) that GGUF goes to FILE and is kept. A GGUF
#          file as input (1.x: target_module.sh --input FILE.gguf) is not converted: the target stage quantizes it.
#   rkllm  the format helper rkllm_module.sh (RKLLM-Toolkit, NPU of the RK3588 and RK3576): the finished .rkllm, which
#          the toolkit quantizes itself
#   rknn   the format helper rknn_module.sh (RKNN-Toolkit2, ONNX models): the finished .rknn
# Job imatrix: the importance matrix of a GGUF build (F16 intermediate, then llama-imatrix over 100 chunks), which the
# BuildEngine hands to the build job (USE_IMATRIX, IMATRIX_PATH).
#
# Around every conversion, what the convert module of version 1.x promised in the maintainer's design (HOW2_ORIGINAL;
# DECISIONS round 16, U9 addendum), done by scripts/model_check.py (the image's python3, standard library only):
#   detection   the input is a Hugging Face folder (SafeTensors or PyTorch weights), a GGUF file (nothing to convert),
#               an ONNX model, a single weights file or a folder of GGUF files; what the format of the build cannot
#               use is refused with the reason before anything slow runs. ONNX for GGUF: llama.cpp has no converter
#               from ONNX, and the refusal names the format rknn for the NPU instead (ONNX_NEXT, DECISIONS round 17).
#   validation  the warnings of 1.x about missing files (validate_huggingface_model), the integrity of the input (its
#               JSON files, SafeTensors headers against the file sizes, the shards an index lists, PyTorch archives, a
#               GGUF header) and of the GGUF the converter wrote (every tensor's data inside the file)
#   statistics  input and output size, ratio and duration: in the log and in convert_stats.json in the stage folder,
#               which the target stage puts into build_info.json ("conversion")
#   test        a short run of the written GGUF with llama-completion (the llama-cli of 1.x; llama-cli itself when the
#               image has only that): 8 tokens, at most FUNCTIONAL_TEST_SECONDS, no network. A run that ends with an
#               error fails the stage; a timeout is a note. Skipped with a note when the image has neither tool, for
#               an embedding model, for the NPU formats and in the imatrix job (llama-imatrix runs the model there).
#   cleanup     a file a converter replaces is kept as FILE.convert-backup until the new one passed its checks; a
#               failed or stopped stage removes what it wrote and puts the backups back (after a killed run: at the
#               next start of the stage).

set -euo pipefail

MODULES_DIR="${MODULES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# The GGUF types and their names come from the stage before (config_module.sh defines, its main runs only there).
# shellcheck source=config_module.sh
source "$MODULES_DIR/config_module.sh"
QUANTIZE_JOBS="$PLAN_DIR/quantize_jobs.sh"
# The stage's own files: its statistics (read by the target stage) and the files it wrote but has not kept yet.
CONVERT_STATS="$PLAN_DIR/convert_stats.json"
CONVERT_STATE="$PLAN_DIR/convert_state.json"
MODEL_CHECK="$(dirname "$MODULES_DIR")/scripts/model_check.py"
FUNCTIONAL_TEST=1
FUNCTIONAL_TEST_SECONDS=120
# Where an ONNX model goes on this target: named when an ONNX model is refused for GGUF or RKLLM.
ONNX_NEXT="ONNX models, such as the voice and vision models piper-tts, vosk or whisper, are built for the NPU of the \
Rockchip boards in the format rknn (RKNN-Toolkit2); on the RK3566 the NPU runs voice and vision models, and the LLM \
runs on the CPU as a GGUF."

log_warn() { echo "WARNING: $*" >&2; }

model_check() {
    [ -f "$MODEL_CHECK" ] || die "The checks of the convert stage are missing: scripts/model_check.py is not in the image."
    python3 "$MODEL_CHECK" "$@"
}

# ============================================================================
# VALIDATION LOGIC
# ============================================================================

# What the input is, and whether it is complete (model_check.py input): refuses what this format cannot use.
check_input() {
    model_check input "$MODEL_SOURCE" --format "$FORMAT" --stats "$CONVERT_STATS" --onnx-next "$ONNX_NEXT"
}

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
# CLEANUP ON FAILURE
# ============================================================================

# A failed or stopped stage leaves nothing half-written: what this run wrote goes, the files it replaced come back.
# The stage keeps its exit code.
on_exit() {
    local rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -f "$MODEL_CHECK" ]; then
        python3 "$MODEL_CHECK" restore --state "$CONVERT_STATE" --failed || true
    fi
    exit "$rc"
}

# ============================================================================
# GGUF
# ============================================================================

convert_to_gguf() {
    local outtype="$1" outfile="$2" started
    [ -f "$CONVERT_SCRIPT" ] || die "llama.cpp converter not found at $CONVERT_SCRIPT"
    model_check begin "$outfile" --state "$CONVERT_STATE" --scratch "$SCRATCH_DIR"
    echo ">> Converting HF -> GGUF ($outtype)..."
    started=$SECONDS
    python3 "$CONVERT_SCRIPT" "$MODEL_SOURCE" --outfile "$outfile" --outtype "$outtype"
    check_gguf_output "$outfile" "$outtype" "$((SECONDS - started))"
}

# OUTFILE OUTTYPE SECONDS: the GGUF the converter wrote, checked (its tensors inside the file), tested (a short run,
# except in the imatrix job) and counted (the statistics).
check_gguf_output() {
    local test_args=(--tools "$LLAMA_BASE/build/bin" --test-seconds "$FUNCTIONAL_TEST_SECONDS")
    if [ "$FUNCTIONAL_TEST" = "0" ]; then
        test_args=(--test-skip "llama-imatrix runs the model over the calibration text next")
    fi
    model_check output "$1" --format gguf --type "$(normalize_gguf_type "$2")" --seconds "$3" \
        --stats "$CONVERT_STATS" "${test_args[@]}"
}

# The model for one GGUF type: written directly by the converter, or an F16 intermediate that the target stage
# quantizes (quantize_jobs.sh lists what it has to do). With --output (CONVERT_OUTPUT, convert_module.sh --output of
# version 1.x) the converter writes that file instead, and it is kept. A GGUF input (the F16 GGUF that
# target_module.sh of 1.x took) is converted already: the target stage quantizes the file itself and keeps it.
build_gguf() {
    local q f16_file out_file job="quantize_gguf"
    q="$(normalize_gguf_type "${1:-$QUANT}")"
    local q_lower
    q_lower="$(echo "$q" | tr '[:upper:]' '[:lower:]')"

    if [ "${INPUT_FORMAT:-model}" = "gguf" ]; then
        [[ "$GGUF_QUANT_TYPES" == *" $q "* ]] || die "The model is a GGUF file already; the target stage quantizes it to a type of llama-quantize, not '$q' (supported:${GGUF_QUANT_TYPES% })."
    elif [[ "$GGUF_CONVERT_TYPES" == *" $q_lower "* ]]; then
        out_file="${CONVERT_OUTPUT:-$OUTPUT_DIR/${MODEL_NAME}-${q}.gguf}"
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

    out_file="$OUTPUT_DIR/${MODEL_NAME}-${q}.gguf"
    if [ "${INPUT_FORMAT:-model}" = "gguf" ]; then
        f16_file="$MODEL_SOURCE"
        job="quantize_kept_gguf"
    else
        f16_file="${CONVERT_OUTPUT:-$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf}"
        if [ -n "${CONVERT_OUTPUT:-}" ]; then
            job="quantize_kept_gguf"
        fi
        convert_to_gguf f16 "$f16_file"
        stage_output "$f16_file"
    fi
    printf '%s %q %q %q' "$job" "$q" "$f16_file" "$out_file" >> "$QUANTIZE_JOBS"
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
    check_input
    FUNCTIONAL_TEST=0
    if [ "${INPUT_FORMAT:-model}" = "gguf" ]; then
        f16_file="$MODEL_SOURCE"  # measured as it is, and kept
    else
        f16_file="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
        convert_to_gguf f16 "$f16_file"
    fi
    echo ">> Computing importance matrix..."
    model_check begin "$IMATRIX_DIR/imatrix.dat" --state "$CONVERT_STATE"
    # -o *.dat keeps the legacy format, which llama-quantize --imatrix accepts.
    "$IMATRIX_BIN" -m "$f16_file" -f "$dataset" -o "$IMATRIX_DIR/imatrix.dat" --chunks 100
    if [ "${INPUT_FORMAT:-model}" != "gguf" ]; then
        rm -f "$f16_file"
    fi
    stage_output "$IMATRIX_DIR/imatrix.dat"
}

# ============================================================================
# RKLLM AND RKNN (the format helpers)
# ============================================================================

# FORMAT SECONDS: the model the format helper wrote into the output folder (new since `model_check watch`), counted.
# Its functional test is the board's: these models run on the NPU only.
check_helper_output() {
    model_check output --watched --state "$CONVERT_STATE" --format "$1" --seconds "$2" --stats "$CONVERT_STATS" \
        --test-skip "a model for the NPU runs on the board only, where it is measured"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    local started
    # shellcheck source=/dev/null
    source "$BUILD_PLAN"
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # A run of this stage that was stopped before it finished: what it wrote goes before anything else.
    model_check restore --state "$CONVERT_STATE" --stopped

    if [[ "$JOB" == "imatrix" ]]; then
        build_imatrix
        model_check keep --state "$CONVERT_STATE"
        stage_output "$CONVERT_STATS"
        return
    fi

    : > "$QUANTIZE_JOBS"
    case "$FORMAT" in
        gguf)
            check_input
            validate_huggingface_model "$MODEL_SOURCE"
            build_gguf
            ;;
        rkllm)
            check_input
            validate_huggingface_model "$MODEL_SOURCE"
            model_check watch "$OUTPUT_DIR" --state "$CONVERT_STATE"
            started=$SECONDS
            bash "$MODULES_DIR/rkllm_module.sh"
            check_helper_output rkllm "$((SECONDS - started))"
            ;;
        rknn)
            check_input
            model_check watch "$OUTPUT_DIR" --state "$CONVERT_STATE"
            started=$SECONDS
            bash "$MODULES_DIR/rknn_module.sh"
            check_helper_output rknn "$((SECONDS - started))"
            ;;
        *)
            die "Format '$FORMAT' is not supported by the Rockchip target (gguf, rkllm, rknn)."
            ;;
    esac
    model_check keep --state "$CONVERT_STATE"
    stage_output "$CONVERT_STATS"
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
