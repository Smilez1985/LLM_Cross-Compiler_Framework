#!/bin/bash
# rknn_module.sh - ONNX model -> .rknn for a Rockchip NPU: a format helper the convert stage of [MODULE_NAME]
# can run (as targets/Rockchip's convert_module.sh does for the format rknn). It needs RKNN-Toolkit2 in a venv
# of the image (RKNN_PYTHON; targets/Rockchip/dockerfile installs the pinned version) and refuses without it:
# a build has no network, so nothing is cloned or installed here. Below this header the same script as
# targets/Rockchip/modules/rknn_module.sh (tests/test_target_template.py compares them).

set -euo pipefail

MODEL_SOURCE="${MODEL_SOURCE:-}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="${OUTPUT_DIR:-${BUILD_CACHE_DIR}/output}"
RKNN_PYTHON="${RKNN_PYTHON:-/opt/venv-rknn/bin/python}"
CONVERTER="/app/scripts/rknn_converter.py"
BOARD="$(echo "${TARGET_BOARD:-rk3588}" | tr '[:upper:]' '[:lower:]')"

log() { echo ">> [RKNN] $*"; }
die() { echo ">> [RKNN] ERROR: $*" >&2; exit 1; }

[ -x "$RKNN_PYTHON" ] || die "RKNN toolkit venv not found at $RKNN_PYTHON: the image needs RKNN-Toolkit2 in that venv (targets/Rockchip/dockerfile installs the pinned version); a build has no network, so nothing is installed here."

# Input: an .onnx file, or a directory containing exactly one.
ONNX_FILE="$MODEL_SOURCE"
if [ -d "$MODEL_SOURCE" ]; then
    mapfile -t found < <(find "$MODEL_SOURCE" -maxdepth 2 -name '*.onnx' | sort)
    [ "${#found[@]}" -eq 1 ] || die "Expected one .onnx file in $MODEL_SOURCE, found ${#found[@]}. RKNN converts ONNX models; LLMs use the rkllm format."
    ONNX_FILE="${found[0]}"
fi
[[ "$ONNX_FILE" == *.onnx && -f "$ONNX_FILE" ]] || die "RKNN needs an ONNX model, got '$MODEL_SOURCE'."

case "$(echo "${QUANTIZATION:-fp16}" | tr '[:upper:]' '[:lower:]')" in
    i8|int8|q8|q8_0) DTYPE="i8" ;;
    ""|fp16|f16) DTYPE="fp16" ;;
    *) die "Unsupported RKNN quantization '${QUANTIZATION}' (i8, fp16)." ;;
esac

args=(--model "$ONNX_FILE" --target "$BOARD" --dtype "$DTYPE")
if [ "$DTYPE" = "i8" ]; then
    [ -f "${DATASET_PATH:-}" ] || die "INT8 needs a calibration dataset (text file listing sample inputs, one per line)."
    args+=(--dataset "$DATASET_PATH")
fi

NAME="$(basename "$ONNX_FILE" .onnx)"
OUTPUT_FILE="$OUTPUT_DIR/${NAME}-${BOARD}-${DTYPE}.rknn"
log "Converting $NAME for $BOARD ($DTYPE)..."
"$RKNN_PYTHON" "$CONVERTER" "${args[@]}" --output "$OUTPUT_FILE"
[ -f "$OUTPUT_FILE" ] || die "Converter finished but $OUTPUT_FILE was not written."
log "Artifact created: $OUTPUT_FILE"
