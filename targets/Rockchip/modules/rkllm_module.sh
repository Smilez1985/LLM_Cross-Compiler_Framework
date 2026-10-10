#!/bin/bash
# rkllm_module.sh - HF model -> .rkllm for the RK3588 / RK3576 NPU (called by build.sh)

set -euo pipefail

MODEL_SOURCE="${MODEL_SOURCE:-}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="${OUTPUT_DIR:-${BUILD_CACHE_DIR}/output}"
RKLLM_PYTHON="${RKLLM_PYTHON:-/opt/venv-rkllm/bin/python}"
CONVERTER="/app/scripts/export_rkllm.py"
BOARD="$(echo "${TARGET_BOARD:-rk3588}" | tr '[:upper:]' '[:lower:]')"
MODEL_NAME="${MODEL_NAME:-$(basename "${MODEL_SOURCE%/}")}"

log() { echo ">> [RKLLM] $*"; }
die() { echo ">> [RKLLM] ERROR: $*" >&2; exit 1; }

[ -d "$MODEL_SOURCE" ] || die "MODEL_SOURCE '$MODEL_SOURCE' must be a HuggingFace model directory."
[ -x "$RKLLM_PYTHON" ] || die "RKLLM toolkit venv not found at $RKLLM_PYTHON"
[ -f "$CONVERTER" ] || die "Converter $CONVERTER missing."

case "$BOARD" in
    *rk3576*) PLATFORM="rk3576" ;;
    *rk3588*) PLATFORM="rk3588" ;;
    *) die "Unsupported board '$BOARD' for RKLLM (rk3588, rk3576)." ;;
esac

# Accept framework-wide aliases; everything else must be a native RKLLM dtype.
Q="$(echo "${QUANTIZATION:-w8a8}" | tr '[:upper:]' '[:lower:]')"
case "$Q" in
    ""|int8|q8|q8_0) Q="w8a8" ;;
    int4|q4|q4_0|q4_k_m) Q="w4a16" ;;
esac

OUTPUT_FILE="$OUTPUT_DIR/${MODEL_NAME}-${PLATFORM}-${Q}.rkllm"
log "Converting $MODEL_NAME for $PLATFORM ($Q)..."

args=(--model "$MODEL_SOURCE" --output "$OUTPUT_FILE" --quant "$Q" --target "$PLATFORM")
if [ -n "${DATASET_PATH:-}" ] && [ -f "${DATASET_PATH}" ]; then
    args+=(--dataset "$DATASET_PATH")
fi
if [ -n "${MAX_CONTEXT_LENGTH:-}" ]; then
    args+=(--max-context "$MAX_CONTEXT_LENGTH")
fi

"$RKLLM_PYTHON" "$CONVERTER" "${args[@]}"
[ -f "$OUTPUT_FILE" ] || die "Converter finished but $OUTPUT_FILE was not written."
log "Artifact created: $OUTPUT_FILE"
