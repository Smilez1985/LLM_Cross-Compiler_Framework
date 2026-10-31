# shellcheck shell=sh
# runner.sh - the framework's stage runner (part of the runtime layer; tools.sh says what that is)
#
# The host starts it in every build container through the layer's entrypoint.sh, as the stage runner's user with only
# CAP_SETUID and CAP_SETGID; it runs on the layer's own busybox sh and never on a program of the image. A target no
# longer has a runner of its own: a modules/build.sh that a target or a pack ships is ignored (the person sees a plain
# note) and this runner is used. The person's, the wizard's and Ditto's work is the stage modules, target.yml and the
# toolchain Dockerfile; the runner is the framework's.
#
# A build is the stage modules of the target (/app/modules in the image), in the order the host hands over in
# LLMCF_STAGES, from target.yml (orchestrator/Core/runtime_layer.py stage_order): the four stages source, config,
# convert and target, and the modules a target declares in the fixed slots before convert, between convert and target
# (the rotate slot) and after target. JOB_TYPE=imatrix (the importance matrix of a GGUF build) runs the stages up to
# convert. Each module runs as the stage account without any capability (stage_user.sh run_as_stage), with BASH_ENV
# set to stage_env.sh (stage_output and the cmake check). benchmark_module.sh never runs in a build.
#
# Resume. When a stage has really finished, a marker in $BUILD_SCRATCH_DIR/stages/<job>/ records a digest of its
# inputs and the files it wrote (the request, the toolchain pin of the image and the SHA-256 of every file in modules/
# and scripts/). Started again on the same work folder, a stage whose marker has the same digest and whose files are
# still there is not run again (resumed); every stage after one that runs again runs again. --fresh forgets them.
#
# Progress. One line per stage, read by the BuildEngine (orchestrator/Core/builder.py, STAGE_LINE):
#   === Stage 3/4: convert ===                  started
#   === Stage 3/4: convert done in 41 s ===     finished
#   === Stage 1/4: source resumed ===           finished in an earlier run with the same inputs
#   === Stage 3/4: convert failed (exit 1) ===  stopped; the build ends with the stage's exit code
#
# End record. The very last thing a run does is to write ONE end record, build.end, in the end folder the host mounts
# at $LLMCF_END_DIR (written to a temporary file and moved into place): status done|failed, exit N, stage NAME, job,
# inputs DIGEST, time, end protected|unprotected. No key exists in the container: only the runner's uid may write the
# end folder (the stage account cannot: the preflight checks it), and the host trusts "done" only from that folder,
# owned by the runner, together with the container's real exit status and its inspect check
# (orchestrator/Core/build_end.py). A failing stage, a refused loop and every `set -e` path write "failed" through the
# same writer (an EXIT trap); a run killed with SIGKILL leaves no record, which reads as "not finished". Started by hand
# without an end folder, it writes stages/<job>/build.end in the work folder (never trusted by the host).
#
# Cancel. A person's or an AI's Cancel writes the note stages/<job>/cancelled before the framework stops the
# container: the stage that was running then does not count as a restart of the loop protection.
#
# Usage: runner.sh [--job build|imatrix] [--to STAGE] [--fresh] [--input PATH] [--output FILE] [--model-name NAME]
#                  [--quantization TYPE] [--hardware-config FILE] [--package]
#   --to STAGE   run the stages up to STAGE and stop there
#   --fresh      run every stage, also the ones a marker says have finished
#   --input, --model-name, --quantization, --hardware-config
#                set MODEL_SOURCE, MODEL_NAME, QUANTIZATION and HARDWARE_CONFIG_FILE. The input may also be a GGUF
#                file: the convert stage then converts nothing, and the target stage quantizes that file and keeps it.
#   --output FILE  where the convert stage writes its GGUF; a file named here is kept
#   --package    the target stage also makes the package for the board (an input of the target stage only)
# A path given as an option is made absolute. A module started by hand passes its options here. Results go to
# $BUILD_CACHE_DIR/output, intermediates to $BUILD_SCRATCH_DIR (never /tmp, a small tmpfs in the build sandbox).
#
# POSIX sh (busybox sh in the container; any POSIX sh by hand).

set -eu
# shellcheck disable=SC3040 # pipefail where the shell has it (busybox sh, bash)
(set -o pipefail) 2>/dev/null && set -o pipefail
unset IFS   # the default field splitting (space, tab, newline), whatever the environment said

case "$0" in
    */*) LLMCF_LAYER="${0%/*}" ;;
    *) LLMCF_LAYER="." ;;
esac
LLMCF_LAYER="$(cd "$LLMCF_LAYER" && pwd)"
LLMCF_IMAGE_PATH="${LLMCF_IMAGE_PATH:-$PATH}"
export LLMCF_LAYER LLMCF_IMAGE_PATH
# shellcheck source=tools.sh
. "$LLMCF_LAYER/tools.sh"
# shellcheck source=stage_user.sh
. "$LLMCF_LAYER/stage_user.sh"

MODULES_DIR="${LLMCF_MODULES_DIR:-/app/modules}"
SCRIPTS_DIR="$(dirname "$MODULES_DIR")/scripts"
TARGET_LABEL="${LLMCF_TARGET_LABEL:-this target}"

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
    echo "Usage: runner.sh [--job build|imatrix] [--to STAGE] [--fresh] [--input PATH] [--output FILE]"
    echo "                 [--model-name NAME] [--quantization TYPE] [--hardware-config FILE] [--package]"
    echo "Runs the stage modules of this target in the order of its target.yml; see the header of runner.sh."
}

need_value() {  # OPTION COUNT: a refusal when the option has no value
    [ "$2" -ge 2 ] || die "$1 needs a value (runner.sh --help lists the options)."
}

path_of() {  # VALUE: the value as an absolute path (a stage may change its folder)
    case "$1" in
        /*|"") printf '%s' "$1" ;;
        *) printf '%s/%s' "$PWD" "$1" ;;
    esac
}

STOP_AT=""
FRESH=0
CONVERT_OUTPUT=""
PACKAGE_REQUESTED=0
JOB_OPTION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --job) need_value "$1" $#; JOB_OPTION="$2"; shift 2 ;;
        --to) need_value "$1" $#; STOP_AT="$2"; shift 2 ;;
        --fresh) FRESH=1; shift ;;
        --input) need_value "$1" $#; MODEL_SOURCE="$(path_of "$2")"; export MODEL_SOURCE; shift 2 ;;
        --output) need_value "$1" $#; CONVERT_OUTPUT="$(path_of "$2")"; shift 2 ;;
        --model-name) need_value "$1" $#; MODEL_NAME="$2"; export MODEL_NAME; shift 2 ;;
        --quantization) need_value "$1" $#; QUANTIZATION="$2"; export QUANTIZATION; shift 2 ;;
        --hardware-config) need_value "$1" $#; HARDWARE_CONFIG_FILE="$(path_of "$2")"; export HARDWARE_CONFIG_FILE
            shift 2 ;;
        --package) PACKAGE_REQUESTED=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option '$1' (runner.sh --help lists the options)." ;;
    esac
done

# --- The stages of this job ---------------------------------------------------------------------------------------

JOB="build"
if [ "${JOB_OPTION:-${JOB_TYPE:-build}}" = "imatrix" ]; then
    JOB="imatrix"
fi
JOB_TYPE="$JOB"
export JOB_TYPE

valid_stage_name() {  # NAME: a module name a stage list may hold (stage_order on the host has the same rule)
    case "$1" in
        ''|*[!a-z0-9_]*|[!a-z]*) return 1 ;;
    esac
    [ "${#1}" -le 32 ]
}

if [ -n "${LLMCF_STAGES:-}" ]; then
    STAGES="$LLMCF_STAGES"
else
    # by hand, without the host's order: the four stages and the rotate slot when its module is there
    STAGES="source config convert"
    if [ "$JOB" = "build" ]; then
        if [ -f "$MODULES_DIR/rotate_module.sh" ]; then
            STAGES="$STAGES rotate"
        fi
        STAGES="$STAGES target"
    fi
fi
TOTAL=0
for name in $STAGES; do
    valid_stage_name "$name" || die "'$name' is not a valid stage module name."
    [ "$name" != benchmark ] || die "benchmark_module.sh never runs in a build (it would measure this build computer)."
    [ -f "$MODULES_DIR/${name}_module.sh" ] || die "The stage module ${name}_module.sh is missing in $MODULES_DIR."
    TOTAL=$((TOTAL + 1))
done
[ "$TOTAL" -gt 0 ] || die "This build has no stages."

stage_at() {  # N (1-based): the N-th stage
    _k=0
    for _s in $STAGES; do
        _k=$((_k + 1))
        if [ "$_k" -eq "$1" ]; then
            printf '%s' "$_s"
            return 0
        fi
    done
    return 1
}

is_stage() {  # NAME: one of this build's stages
    for _s in $STAGES; do
        [ "$_s" != "$1" ] || return 0
    done
    return 1
}

LAST=$TOTAL
if [ -n "$STOP_AT" ]; then
    LAST=0
    k=0
    for name in $STAGES; do
        k=$((k + 1))
        if [ "$name" = "$STOP_AT" ]; then
            LAST=$k
        fi
    done
    [ "$LAST" -gt 0 ] || die "'$STOP_AT' is not a stage of this build ($STAGES)."
fi

WORK_DIR="${BUILD_SCRATCH_DIR:-${BUILD_CACHE_DIR:-/build-cache}}"
STAGE_DIR="$WORK_DIR/stages/$JOB"
mkdir -p "$STAGE_DIR"

# --- The end record (see the header) -----------------------------------------------------------------------------
if [ -n "${LLMCF_END_DIR:-}" ]; then
    END_RECORD="$LLMCF_END_DIR/build.end"
else
    END_RECORD="$STAGE_DIR/build.end"
fi
END_STAGE=""
END_WRITTEN=0
DIGEST=""
rm -f "$END_RECORD" "$END_RECORD.tmp"   # a record of an earlier run never ends this one

runner_write() {  # FILE: stdin into FILE, through a new temporary file of the runner's own (never through a stage's)
    _tmp="$(mktemp "$1.XXXXXX")" || return 1
    if ! cat > "$_tmp" || ! mv -f "$_tmp" "$1"; then
        rm -f "$_tmp"
        return 1
    fi
}

write_end() {  # STATUS EXIT: the one writer of the end record; fails when the record could not be put in place
    END_WRITTEN=1
    rm -rf -- "$END_RECORD.tmp" "$END_RECORD" 2>/dev/null || true
    if ! printf 'status %s\nexit %s\nstage %s\njob %s\ninputs %s\ntime %s\nend %s\n' "$1" "$2" "${END_STAGE:--}" \
            "$JOB" "${DIGEST:--}" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$STAGE_END_PROTECTION" \
            | runner_write "$END_RECORD"; then
        echo "ERROR: The end record could not be written; the build is not counted as finished." >&2
        return 1
    fi
    chmod 0644 "$END_RECORD" 2>/dev/null || true
}

on_exit() {  # every way the run ends that did not write its record: a failure (set -e, a refusal, a signal)
    rc=$?
    if [ "$END_WRITTEN" = 0 ]; then
        [ "$rc" -ne 0 ] || rc=1
        write_end failed "$rc" || true
    fi
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
STAGE_RUNNER=1
STAGE_LAST="$(stage_at "$LAST")"
export STAGE_RUNNER STAGE_DIR MODULES_DIR STAGE_LAST CONVERT_OUTPUT PACKAGE_REQUESTED

# --- Resume markers -------------------------------------------------------------------------------------------------

file_digest() {
    if [ -n "$1" ] && [ -f "$1" ]; then
        sha256sum "$1" | cut -d' ' -f1
    else
        echo "-"
    fi
}

file_size() { wc -c < "$1" | tr -d ' '; }

model_files() {  # every file of the model with its size and modification time
    _source="${MODEL_SOURCE:-}"
    if [ -z "$_source" ] || [ ! -e "$_source" ]; then
        echo "model: none"
        return 0
    fi
    { find -L "$_source" -type f 2>/dev/null || true; } | LC_ALL=C sort | while IFS= read -r _f; do
        printf 'model: %s %s\n' "${_f#"$_source"}" "$(stat -L -c '%s %Y' "$_f" 2>/dev/null || file_size "$_f")"
    done
}

inputs_digest() {
    {
        echo "stage-runner 2, job $JOB, stages $STAGES"
        printf 'format=%s board=%s quantization=%s\n' "${TARGET_FORMAT:-}" "${TARGET_BOARD:-}" "${QUANTIZATION:-}"
        printf 'model=%s name=%s revision=%s\n' "${MODEL_SOURCE:-}" "${MODEL_NAME:-}" "${MODEL_REVISION:-}"
        printf 'context=%s imatrix=%s %s %s\n' "${MAX_CONTEXT_LENGTH:-}" "${USE_IMATRIX:-}" "${IMATRIX_PATH:-}" \
            "$(file_digest "${IMATRIX_PATH:-}")"
        printf 'dataset=%s %s\n' "${DATASET_PATH:-}" "$(file_digest "${DATASET_PATH:-}")"
        # The profile config_module.sh reads, also the one it reads when none is named
        _profile="${HARDWARE_CONFIG_FILE:-${BUILD_CACHE_DIR:-/build-cache}/target_hardware_config.txt}"
        printf 'hardware=%s %s\n' "$_profile" "$(file_digest "$_profile")"
        printf 'output=%s %s %s\n' "${BUILD_CACHE_DIR:-}" "${OUTPUT_DIR:-}" "$CONVERT_OUTPUT"
        printf 'pythonpath=%s\n' "${PYTHONPATH:-}"
        if [ -z "${MODEL_SOURCE:-}" ] && [ "$STAGE_LAST" = config ]; then
            echo "a plan without a model"  # config_module.sh --hardware-config: never resumed into a build
        fi
        printf 'llama.cpp=%s %s %s\n' "${LLAMA_CPP_PATH:-}" \
            "$(file_digest "${LLAMA_CPP_PATH:-/usr/src/llama.cpp}/.llama_cpp_tag")" \
            "$(file_digest "${LLAMA_CPP_PATH:-/usr/src/llama.cpp}/.llama_cpp_commit")"
        printf 'sdk=%s %s\n' "${RKLLM_PYTHON:-}" "${RKNN_PYTHON:-}"
        model_files
        for _f in "$MODULES_DIR"/* "$SCRIPTS_DIR"/*; do
            if [ -f "$_f" ]; then
                printf 'script: %s %s\n' "${_f#"$(dirname "$MODULES_DIR")"/}" "$(file_digest "$_f")"
            fi
        done
    } | sha256sum | cut -d' ' -f1
}

stage_inputs() {  # STAGE: the digest of its inputs; --package is an input of the target stage only
    if [ "$1" = target ] && [ "$PACKAGE_REQUESTED" = 1 ]; then
        echo "$DIGEST+package"
    else
        echo "$DIGEST"
    fi
}

marker_matches() {  # STAGE: finished with the same inputs
    [ -f "$STAGE_DIR/$1.done" ] && [ "$(sed -n 's/^inputs //p' "$STAGE_DIR/$1.done")" = "$(stage_inputs "$1")" ]
}

outputs_present() {  # STAGE: every file the stage wrote is still there, with the same size
    [ -f "$STAGE_DIR/$1.done" ] || return 1
    while IFS=' ' read -r _kind _size _path; do
        if [ "$_kind" = "output" ]; then
            [ -f "$_path" ] && [ "$(file_size "$_path")" = "$_size" ] || return 1
        fi
    done < "$STAGE_DIR/$1.done"
}

write_marker() {  # STAGE: written only after the stage exited 0
    {
        echo "stage $1"
        echo "inputs $(stage_inputs "$1")"
        echo "finished $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        if [ -f "$STAGE_DIR/$1.outputs" ]; then
            LC_ALL=C sort -u "$STAGE_DIR/$1.outputs" | while IFS= read -r _f; do
                if [ -f "$_f" ]; then
                    echo "output $(file_size "$_f") $_f"
                fi
            done
        fi
    } | runner_write "$STAGE_DIR/$1.done"
}

# --- Loop protection -----------------------------------------------------------------------------------------------
# A stage that was started with the same inputs and never finished (stopped and resumed again and again) is a loop,
# not a long build: no clock stops it, this count does. STAGE.starts holds the inputs and how often the stage started
# with them; a finished stage removes it, --fresh forgets it.
STAGE_RESTART_LIMIT="${STAGE_RESTART_LIMIT:-3}"
case "$STAGE_RESTART_LIMIT" in ''|*[!0-9]*) STAGE_RESTART_LIMIT=3 ;; esac

count_start() {  # STAGE: records one more start; fails when the stage already started too often without finishing
    _file="$STAGE_DIR/$1.starts"
    _inputs="$(stage_inputs "$1")"
    _count=0
    if [ -f "$_file" ] && [ "$(sed -n 's/^inputs //p' "$_file")" = "$_inputs" ]; then
        _count="$(sed -n 's/^count //p' "$_file")"
        case "$_count" in ''|*[!0-9]*) _count=0 ;; esac
    fi
    if [ "$_count" -ge "$STAGE_RESTART_LIMIT" ]; then
        echo "ERROR: The stage $1 started $_count times with the same inputs and never finished; it is not started again (a loop, not a long build). Look at its log for the cause, then start the build again with --fresh." >&2
        return 1
    fi
    printf 'inputs %s\ncount %s\n' "$_inputs" "$((_count + 1))" | runner_write "$_file"
}

# A person's or an AI's Cancel is not a restart: the stage that was running when the framework wrote the cancel note
# (stages/<job>/cancelled) gives back the start it counted. The note is removed; a stop without it still counts.
forget_cancelled_start() {
    [ -f "$STAGE_DIR/cancelled" ] || return 0
    _stage="$(sed -n '1p' "$STAGE_DIR/running" 2>/dev/null || true)"
    _file="$STAGE_DIR/$_stage.starts"
    if [ -n "$_stage" ] && is_stage "$_stage" && [ -f "$_file" ]; then
        _count="$(sed -n 's/^count //p' "$_file")"
        case "$_count" in ''|*[!0-9]*) _count=0 ;; esac
        if [ "$_count" -le 1 ]; then
            rm -f "$_file"
        else
            printf 'inputs %s\ncount %s\n' "$(sed -n 's/^inputs //p' "$_file")" "$((_count - 1))" | runner_write "$_file"
        fi
    fi
    rm -f "$STAGE_DIR/cancelled" "$STAGE_DIR/running"
}

# run_module NAME: one stage module as the stage account, with the image's PATH and the layer's stage_env.sh; what the
# host handed the runner alone (its ids, the end folder, the stage order, the target's label) does not reach a module
HOST_ONLY="-u STAGE_RUNNER_IDS -u LLMCF_END_DIR -u LLMCF_STAGES -u LLMCF_TARGET_LABEL"
run_module() {
    # shellcheck disable=SC2086 # HOST_ONLY is a list of options
    if layer_has_tools; then
        run_as_stage "$BB" env $HOST_ONLY PATH="$LLMCF_IMAGE_PATH" BASH_ENV="$LLMCF_LAYER/stage_env.sh" \
            bash "$MODULES_DIR/${1}_module.sh"
    else
        run_as_stage env $HOST_ONLY PATH="$LLMCF_IMAGE_PATH" BASH_ENV="$LLMCF_LAYER/stage_env.sh" \
            bash "$MODULES_DIR/${1}_module.sh"
    fi
}

# --- Run ------------------------------------------------------------------------------------------------------------

echo "=== Build Started: $TARGET_LABEL (stages: $STAGES) ==="

forget_cancelled_start
rm -f "$STAGE_DIR/running"
# The build sandbox's stage account: the runner's rights, a test switch, the folders the modules write (stage_user.sh)
END_STAGE="preflight"
stage_preflight "$WORK_DIR" "$STAGE_DIR" "${BUILD_CACHE_DIR:-/build-cache}/output" "${BUILD_CACHE_DIR:-/build-cache}/imatrix"
if ! stage_isolated; then
    echo "=== Isolation: stage modules run as the stage runner's own user (outside the build sandbox) ==="
fi
END_STAGE=""
DIGEST="$(inputs_digest)"
# The first stage to run: the first one without a matching marker, moved back over every finished stage whose files
# are gone (the stage after it needs them). With every marker matching, the last stage's files decide.
START=1
if [ "$FRESH" = "0" ]; then
    while [ "$START" -le "$LAST" ] && marker_matches "$(stage_at "$START")"; do
        START=$((START + 1))
    done
    if [ "$START" -gt "$LAST" ] && ! outputs_present "$(stage_at "$LAST")"; then
        START=$LAST
    fi
    while [ "$START" -gt 1 ] && ! outputs_present "$(stage_at $((START - 1)))"; do
        START=$((START - 1))
    done
fi
if [ "$FRESH" = "1" ]; then
    rm -f "$STAGE_DIR"/*.starts
fi
# A stage that runs makes every later marker stale; when every stage up to --to resumes, the later ones stay valid.
if [ "$START" -le "$LAST" ]; then
    k=0
    for name in $STAGES; do
        k=$((k + 1))
        if [ "$k" -ge "$START" ]; then
            rm -f "$STAGE_DIR/$name.done"
        fi
    done
fi

i=0
for name in $STAGES; do
    i=$((i + 1))
    [ "$i" -le "$LAST" ] || break
    number="$i/$TOTAL"
    if [ "$i" -lt "$START" ]; then
        echo "=== Stage $number: $name resumed ==="
        continue
    fi
    echo "=== Stage $number: $name ==="
    started="$(date +%s)"
    rm -f "$STAGE_DIR/$name.outputs"
    : | runner_write "$STAGE_DIR/$name.outputs"   # the stage's list of its files (stage_output): it may add to it
    chmod 0664 "$STAGE_DIR/$name.outputs"
    END_STAGE="$name"
    if ! count_start "$name"; then
        echo "=== Stage $number: $name failed (exit 75) ===" >&2
        exit 75
    fi
    printf '%s\n' "$name" | runner_write "$STAGE_DIR/running"
    rc=0
    STAGE_NAME="$name"
    export STAGE_NAME
    run_module "$name" || rc=$?
    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -lt 128 ]; then
            rm -f "$STAGE_DIR/$name.starts"   # it ended with its own error; only a stage stopped from outside counts
        fi
        forget_cancelled_start               # stopped by a Cancel: not a restart either
        rm -f "$STAGE_DIR/running"
        echo "=== Stage $number: $name failed (exit $rc) ===" >&2
        exit "$rc"
    fi
    rm -f "$STAGE_DIR/$name.starts" "$STAGE_DIR/running"
    write_marker "$name"
    echo "=== Stage $number: $name done in $(( $(date +%s) - started )) s ==="
done

if [ "$LAST" -lt "$TOTAL" ]; then
    echo "Stopped after the stage $(stage_at "$LAST") (--to)."
elif [ "$JOB" = "imatrix" ]; then
    echo "Done (imatrix)."
else
    echo "Done."
fi
# The very last step: the end record (the stages asked for have finished, with their markers)
END_STAGE="$STAGE_LAST"
write_end "done" 0
