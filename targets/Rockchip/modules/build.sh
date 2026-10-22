#!/bin/bash
# build.sh - the stage runner of the Rockchip target (run by the BuildEngine inside the target image as
# /app/modules/build.sh, and by entrypoint.sh's modes pipeline, source, config, convert and target)
#
# A build is the four modules of this folder, run in this order. Each module's header says which image it runs in,
# which system the result is for and what its stage does; this runner only runs them (DECISIONS U9).
#   1. source   source_module.sh   the toolchain sources the image was built from (llama.cpp pin, SDK venvs)
#   2. config   config_module.sh   checks the request and writes the build plan (build_config.sh in the work folder)
#   3. convert  convert_module.sh  the model in full precision (a GGUF F16 intermediate) or, through the format
#                                  helpers rkllm_module.sh (RKLLM) and rknn_module.sh (RKNN), the finished model
#      rotate   rotate_module.sh   optional hook between convert and target, for a rotation such as SpinQuant or
#                                  QuaRot (roadmap): it runs only when the target has this file, and none is shipped
#   4. target   target_module.sh   quantization, build_info.json and the list of results
# JOB_TYPE=imatrix (the importance matrix of a GGUF build) runs source, config and convert; any other job runs them all.
# benchmark_module.sh never runs in a build: it would measure this build host's CPU, and a model for a board is
# measured on the board (llm-cli device runtime verify --bench-model, or Boards > Boards and Devices... > Verify...).
#
# Resume. When a stage has really finished, a marker in $BUILD_SCRATCH_DIR/stages/<job>/ records a digest of its
# inputs and the files it wrote. The inputs are the request (format, board, quantization, model folder and file
# sizes, model revision and name, context length, importance matrix, calibration dataset, hardware profile), the
# toolchain (the llama.cpp pin of the image, the SDK paths) and the SHA-256 of every file in modules/ and scripts/.
# Started again on the same work folder, a stage whose marker has the same digest and whose files are still there is
# not run again (resumed); every stage after one that runs again runs again. --fresh forgets the markers.
#
# Progress. One line per stage, read by the BuildEngine (orchestrator/Core/builder.py, STAGE_LINE):
#   === Stage 3/4: convert ===                  started
#   === Stage 3/4: convert done in 41 s ===     finished
#   === Stage 1/4: source resumed ===           finished in an earlier run with the same inputs
#   === Stage 3/4: convert failed (exit 1) ===  stopped; the build ends with the stage's exit code
#
# Usage: build.sh [--to STAGE] [--fresh] [--input PATH] [--output FILE] [--model-name NAME] [--quantization TYPE]
#                 [--hardware-config FILE]
#   --to STAGE   run the stages up to STAGE (source, config, convert, rotate or target) and stop there
#   --fresh      run every stage, also the ones a marker says have finished
#   --input, --model-name, --quantization, --hardware-config
#                set MODEL_SOURCE, MODEL_NAME, QUANTIZATION and HARDWARE_CONFIG_FILE (the options of the modules of
#                version 1.x). The input may also be a GGUF file (the F16 GGUF that target_module.sh of 1.x took):
#                the convert stage then converts nothing, and the target stage quantizes that file and keeps it.
#   --output FILE  where the convert stage writes its GGUF (convert_module.sh --output of 1.x): the type the
#                converter writes itself, or the F16 file that the target stage quantizes; a file named here is kept
# A path given as an option is made absolute (from the current folder). A module started by hand passes its options
# here: `convert_module.sh --input DIR --output FILE` is `build.sh --to convert --input DIR --output FILE`, and
# `config_module.sh --hardware-config FILE` needs no model (the plan then holds the hardware profile only).
# The inputs the stages read from the environment are listed in config_module.sh. Results go to
# $BUILD_CACHE_DIR/output, intermediates to $BUILD_SCRATCH_DIR.

set -euo pipefail

TARGET_LABEL="Rockchip"
MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(dirname "$MODULES_DIR")/scripts"

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
    echo "Usage: build.sh [--to STAGE] [--fresh] [--input PATH] [--output FILE] [--model-name NAME]"
    echo "                [--quantization TYPE] [--hardware-config FILE]"
    echo "Runs the stages source, config, convert, (rotate,) target of this target; see the header of build.sh."
}

value_of() {  # OPTION VALUE...: the option's value, or a refusal when it has none
    [ $# -ge 2 ] || die "$1 needs a value (build.sh --help lists the options)."
    printf '%s' "$2"
}

path_of() {  # OPTION VALUE...: the option's value as an absolute path (a stage may change its folder)
    [ $# -ge 2 ] || die "$1 needs a value (build.sh --help lists the options)."
    case "$2" in
        /*|"") printf '%s' "$2" ;;
        *) printf '%s/%s' "$PWD" "$2" ;;
    esac
}

STOP_AT=""
FRESH=0
CONVERT_OUTPUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --to) STOP_AT="$(value_of "$@")"; shift 2 ;;
        --fresh) FRESH=1; shift ;;
        --input) MODEL_SOURCE="$(path_of "$@")"; export MODEL_SOURCE; shift 2 ;;
        --output) CONVERT_OUTPUT="$(path_of "$@")"; shift 2 ;;
        --model-name) MODEL_NAME="$(value_of "$@")"; export MODEL_NAME; shift 2 ;;
        --quantization) QUANTIZATION="$(value_of "$@")"; export QUANTIZATION; shift 2 ;;
        --hardware-config) HARDWARE_CONFIG_FILE="$(path_of "$@")"; export HARDWARE_CONFIG_FILE; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option '$1' (build.sh --help lists the options)." ;;
    esac
done

# --- The stages of this job ---------------------------------------------------------------------------------------

JOB="build"
if [ "${JOB_TYPE:-build}" = "imatrix" ]; then
    JOB="imatrix"
fi
STAGES=(source config convert)
if [ "$JOB" = "build" ]; then
    if [ -f "$MODULES_DIR/rotate_module.sh" ]; then
        STAGES+=(rotate)
    fi
    STAGES+=(target)
fi
TOTAL=${#STAGES[@]}
LAST=$TOTAL
if [ -n "$STOP_AT" ]; then
    LAST=0
    for i in "${!STAGES[@]}"; do
        if [ "${STAGES[$i]}" = "$STOP_AT" ]; then
            LAST=$((i + 1))
        fi
    done
    [ "$LAST" -gt 0 ] || die "'$STOP_AT' is not a stage of this build (${STAGES[*]})."
fi

WORK_DIR="${BUILD_SCRATCH_DIR:-${BUILD_CACHE_DIR:-/build-cache}}"
STAGE_DIR="$WORK_DIR/stages/$JOB"
mkdir -p "$STAGE_DIR"
STAGE_RUNNER=1
STAGE_LAST="${STAGES[$((LAST - 1))]}"
export STAGE_RUNNER STAGE_DIR MODULES_DIR STAGE_LAST CONVERT_OUTPUT

# The stage scripts' one call into the runner: `stage_output FILE` names a file the stage wrote. A resume runs the
# stage again when such a file is gone or has another size.
stage_output() { printf '%s\n' "$1" >> "$STAGE_DIR/$STAGE_NAME.outputs"; }
export -f stage_output

# --- Resume markers -------------------------------------------------------------------------------------------------

file_digest() {
    if [ -f "$1" ]; then
        sha256sum "$1" | cut -d' ' -f1
    else
        echo "-"
    fi
}

file_size() { wc -c < "$1" | tr -d ' '; }

model_files() {  # every file of the model with its size and modification time
    local source="${MODEL_SOURCE:-}" f
    if [ -z "$source" ] || [ ! -e "$source" ]; then
        echo "model: none"
        return 0
    fi
    { find -L "$source" -type f 2>/dev/null || true; } | LC_ALL=C sort | while IFS= read -r f; do
        printf 'model: %s %s\n' "${f#"$source"}" "$(stat -L -c '%s %Y' "$f" 2>/dev/null || file_size "$f")"
    done
}

inputs_digest() {
    local f profile
    {
        echo "stage-runner 1, job $JOB"
        printf 'format=%s board=%s quantization=%s\n' "${TARGET_FORMAT:-}" "${TARGET_BOARD:-}" "${QUANTIZATION:-}"
        printf 'model=%s name=%s revision=%s\n' "${MODEL_SOURCE:-}" "${MODEL_NAME:-}" "${MODEL_REVISION:-}"
        printf 'context=%s imatrix=%s %s %s\n' "${MAX_CONTEXT_LENGTH:-}" "${USE_IMATRIX:-}" "${IMATRIX_PATH:-}" \
            "$(file_digest "${IMATRIX_PATH:-}")"
        printf 'dataset=%s %s\n' "${DATASET_PATH:-}" "$(file_digest "${DATASET_PATH:-}")"
        # The profile config_module.sh reads, also the one it reads when none is named
        profile="${HARDWARE_CONFIG_FILE:-${BUILD_CACHE_DIR:-/build-cache}/target_hardware_config.txt}"
        printf 'hardware=%s %s\n' "$profile" "$(file_digest "$profile")"
        printf 'output=%s %s %s\n' "${BUILD_CACHE_DIR:-}" "${OUTPUT_DIR:-}" "$CONVERT_OUTPUT"
        printf 'pythonpath=%s\n' "${PYTHONPATH:-}"
        if [ -z "${MODEL_SOURCE:-}" ] && [ "$STAGE_LAST" = config ]; then
            echo "a plan without a model"  # config_module.sh --hardware-config: never resumed into a build
        fi
        printf 'llama.cpp=%s %s %s\n' "${LLAMA_CPP_PATH:-}" "$(file_digest "${LLAMA_CPP_PATH:-/usr/src/llama.cpp}/.llama_cpp_tag")" \
            "$(file_digest "${LLAMA_CPP_PATH:-/usr/src/llama.cpp}/.llama_cpp_commit")"
        printf 'sdk=%s %s\n' "${RKLLM_PYTHON:-}" "${RKNN_PYTHON:-}"
        model_files
        for f in "$MODULES_DIR"/* "$SCRIPTS_DIR"/*; do
            if [ -f "$f" ]; then
                printf 'script: %s %s\n' "${f#"$(dirname "$MODULES_DIR")"/}" "$(file_digest "$f")"
            fi
        done
    } | sha256sum | cut -d' ' -f1
}

marker_matches() {  # STAGE: finished with the same inputs
    [ -f "$STAGE_DIR/$1.done" ] && [ "$(sed -n 's/^inputs //p' "$STAGE_DIR/$1.done")" = "$DIGEST" ]
}

outputs_present() {  # STAGE: every file the stage wrote is still there, with the same size
    local kind size path
    [ -f "$STAGE_DIR/$1.done" ] || return 1
    while IFS=' ' read -r kind size path; do
        if [ "$kind" = "output" ]; then
            [ -f "$path" ] && [ "$(file_size "$path")" = "$size" ] || return 1
        fi
    done < "$STAGE_DIR/$1.done"
}

write_marker() {  # STAGE: written only after the stage exited 0
    local stage="$1" f
    {
        echo "stage $stage"
        echo "inputs $DIGEST"
        echo "finished $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        if [ -f "$STAGE_DIR/$stage.outputs" ]; then
            LC_ALL=C sort -u "$STAGE_DIR/$stage.outputs" | while IFS= read -r f; do
                if [ -f "$f" ]; then
                    echo "output $(file_size "$f") $f"
                fi
            done
        fi
    } > "$STAGE_DIR/$stage.done.tmp"
    mv -f "$STAGE_DIR/$stage.done.tmp" "$STAGE_DIR/$stage.done"
}

# --- Run ------------------------------------------------------------------------------------------------------------

echo "=== Build Started: $TARGET_LABEL (stages: ${STAGES[*]}) ==="

DIGEST="$(inputs_digest)"
# The first stage to run: the first one without a matching marker, moved back over every finished stage whose files
# are gone (the stage after it needs them). With every marker matching, the last stage's files decide.
START=0
if [ "$FRESH" = "0" ]; then
    while [ "$START" -lt "$LAST" ] && marker_matches "${STAGES[$START]}"; do
        START=$((START + 1))
    done
    if [ "$START" -eq "$LAST" ] && ! outputs_present "${STAGES[$((LAST - 1))]}"; then
        START=$((LAST - 1))
    fi
    while [ "$START" -gt 0 ] && ! outputs_present "${STAGES[$((START - 1))]}"; do
        START=$((START - 1))
    done
fi
for i in "${!STAGES[@]}"; do
    if [ "$i" -ge "$START" ]; then
        rm -f "$STAGE_DIR/${STAGES[$i]}.done"
    fi
done

for i in "${!STAGES[@]}"; do
    [ "$i" -lt "$LAST" ] || break
    name="${STAGES[$i]}"
    number="$((i + 1))/$TOTAL"
    if [ "$i" -lt "$START" ]; then
        echo "=== Stage $number: $name resumed ==="
        continue
    fi
    echo "=== Stage $number: $name ==="
    started=$SECONDS
    rm -f "$STAGE_DIR/$name.outputs"
    rc=0
    STAGE_NAME="$name" bash "$MODULES_DIR/${name}_module.sh" || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "=== Stage $number: $name failed (exit $rc) ===" >&2
        exit "$rc"
    fi
    write_marker "$name"
    echo "=== Stage $number: $name done in $((SECONDS - started)) s ==="
done

if [ "$LAST" -lt "$TOTAL" ]; then
    echo "Stopped after the stage ${STAGES[$((LAST - 1))]} (--to)."
elif [ "$JOB" = "imatrix" ]; then
    echo "Done (imatrix)."
else
    echo "Done."
fi
