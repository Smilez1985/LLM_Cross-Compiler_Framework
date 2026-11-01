#!/bin/bash
# target_module.sh - stage 4 of the Rockchip build: quantization and the results (run by build.sh, the stage runner)
# Part of LLM Cross-Compiler Framework
#
# Image:  targets/Rockchip/dockerfile: Debian bookworm for the build host's architecture (x86_64); llama-quantize is
#         the image's own (build/bin of the pinned llama.cpp, built for the build host).
# Target: Rockchip boards, aarch64 Linux (RK3566, RK3568: CPU; RK3576, RK3588: CPU and NPU).
# Stage:  quantizes what the convert stage left for it (quantize_jobs.sh: the F16 intermediate to the requested GGUF
#         type, with the importance matrix when the build has one) and removes the intermediate (a GGUF input, the
#         --input FILE.gguf of target_module.sh of version 1.x, and a file named with --output stay); writes
#         build_info.json (model, format, board, quantization, llama.cpp tag), which the BuildEngine completes with
#         the file list, the hashes and the build data; and lists the results.
#         The package for the board (version 1.x's target module: deployment-ready, with documentation, helper
#         scripts, a complete manifest and automated tests), in output/packages/<model>_<type>_<arch>_<time>/: the GGUF,
#         llama-cli and llama-server cross-compiled from the image's llama.cpp source with the plan's toolchain, the
#         toolchain file and the hardware profile, a model card (README.md), test_model.sh and deploy.sh (from
#         scripts/package_*.sh), MANIFEST.json with every build detail and SHA256SUMS (scripts/board_package.py). It is
#         checked before it is kept: the model is run once on this machine with the image's llama-completion (when the
#         image has it), the programs must be ELF programs for the board, and every file must match SHA256SUMS.
#         It is made for a GGUF build with a hardware profile in the build plan (config_module.sh), and on request
#         (build.sh --package: target_module.sh started by hand, the entrypoint modes target and pipeline) also without
#         one, with the plain defaults of this target (plan_package_defaults), which the manifest names. A build of the
#         BuildEngine gets the board's stored probe when there is one (DECISIONS round 23 item 5), and a GGUF build then
#         makes the package too; the board's own llama.cpp runtime still comes from the runtime builder (llm-cli device
#         runtime build).

set -euo pipefail

MODULES_DIR="${MODULES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# The paths of the build plan and die() come from the stage that writes the plan.
# shellcheck source=config_module.sh
source "$MODULES_DIR/config_module.sh"
QUANTIZE_JOBS="$PLAN_DIR/quantize_jobs.sh"
# The convert stage's statistics (input, output, ratio, duration, functional test): "conversion" in build_info.json
CONVERT_STATS="$PLAN_DIR/convert_stats.json"

# Target configuration storage
declare -A TARGET_CONFIG
declare -A QUANTIZATION_STATS
declare -A BUILD_STATS
QUANTIZED_FROM=()

# ============================================================================
# LOGGING & ERROR HANDLING
# ============================================================================

log_info() { echo "ℹ️  [$(date '+%H:%M:%S')] [TARGET] $1"; }
log_success() { echo "✅ [$(date '+%H:%M:%S')] [TARGET] $1"; }
log_warn() { echo "⚠️  [$(date '+%H:%M:%S')] [TARGET] $1"; }

# The name of this target in the package (model card tag, manifest)
PACKAGE_TARGET="Rockchip"

# ============================================================================
# QUANTISIERUNG
# ============================================================================

# One job of quantize_jobs.sh: TYPE F16_FILE OUTPUT_FILE [llama-quantize options, e.g. --imatrix FILE].
# quantize_kept_gguf keeps the F16 file (a GGUF input, or the file named with --output); quantize_gguf removes it
# afterwards (the intermediate of the convert stage).
quantize_kept_gguf() {
    local q="$1" f16_file="$2" out_file="$3"
    shift 3
    echo ">> Quantizing -> $q"
    local started=$SECONDS
    "$QUANTIZE_BIN" "$@" "$f16_file" "$out_file" "$q"
    [ -s "$out_file" ] || die "llama-quantize ended without writing the $q model."
    TARGET_CONFIG[QUANTIZE_SECONDS]=$(( ${TARGET_CONFIG[QUANTIZE_SECONDS]:-0} + SECONDS - started ))
    TARGET_CONFIG[QUANTIZED_GGUF]="$out_file"
    TARGET_CONFIG[QUANT_METHOD]="$q"
}

quantize_gguf() {
    quantize_kept_gguf "$@"
    QUANTIZED_FROM+=("$2")
}

quantize_model() {
    local f16_file
    [ -s "$QUANTIZE_JOBS" ] || return 0
    # shellcheck source=/dev/null
    source "$QUANTIZE_JOBS"
    for f16_file in "${QUANTIZED_FROM[@]}"; do
        rm -f "$f16_file"
    done
}

write_build_info() {
    local conversion=""
    if [ -s "$CONVERT_STATS" ]; then
        conversion=", \"conversion\": $(cat "$CONVERT_STATS")"
    fi
    if [ -n "${TARGET_CONFIG[QUANTIZE_SECONDS]:-}" ]; then   # how long llama-quantize ran (the convert statistics
        conversion="$conversion, \"quantize_seconds\": ${TARGET_CONFIG[QUANTIZE_SECONDS]}"   # have the rest)
    fi
    cat > "$OUTPUT_DIR/build_info.json" <<EOF
{"model": "$MODEL_NAME", "format": "$FORMAT", "board": "$BOARD", "quantization": "${QUANT:-default}", "llama_cpp": "$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)"$conversion}
EOF
}

# ============================================================================
# PACKAGE FOR THE BOARD (version 1.x: "Professional Packaging - deployment-ready with documentation", "Helper
# Scripts - test_model.sh, deploy.sh", "Complete Manifest - JSON with all build details", "Automated Testing -
# functional tests + validation")
# The same functions in targets/Rockchip and targets/_template (tests/test_target_stage.py compares them). They read
# the board's part of the build plan (config_module.sh: board_plan_lines) and PACKAGE_TARGET, the target's name.
# ============================================================================

PACKAGE_SCRIPTS="$(dirname "$MODULES_DIR")/scripts"
FUNCTIONAL_TEST_SECONDS=120
# The options of llama.cpp that download at configure time whenever they are set (NEVER_SET of
# orchestrator/runtime/build/recipe.py, which the image's Dockerfile refuses in LLAMA_CMAKE_ARGS as well;
# tests/test_target_stage.py compares): no option after them turns them off again.
NEVER_SET_OPTIONS=(LLAMA_BUILD_BORINGSSL LLAMA_BUILD_LIBRESSL GGML_CUDA_CCCL_VERSION GGML_ZENDNN GGML_VIRTGPU
                   LLAMA_BUILD_WEBUI LLAMA_USE_PREBUILT_WEBUI)
# The CMake variables that make CMake run a script at project() (SCRIPT_VARIABLE_RE of recipe.py; tests/test_target_stage.py
# compares): the script runs after the command line's options and can set any of them, like -C.
SCRIPT_VARIABLE_PATTERN='^CMAKE_PROJECT_([^[:space:]=:]+_)?INCLUDE(_BEFORE)?$|^CMAKE_PROJECT_TOP_LEVEL_INCLUDES$'
# The options every build sets to OFF (DOWNLOAD_OPTIONS_OFF and DEPRECATED_DOWNLOAD_OPTIONS of recipe.py;
# tests/test_target_stage.py compares): the package's cross build sets them after the module's flags, but a cmake call
# of a module's own scripts has nothing after its words (module_flag_problem --alone).
DOWNLOAD_OFF_OPTIONS=(LLAMA_BUILD_TESTS LLAMA_BUILD_EXAMPLES LLAMA_BUILD_APP LLAMA_USE_PREBUILT_UI LLAMA_BUILD_UI
                      LLAMA_LLGUIDANCE GGML_CPU_KLEIDIAI GGML_OPENMP_FETCH LLAMA_CURL)

# Without a hardware profile in the build plan (a package on request: build.sh --package, target_module.sh started
# by hand): the board's part of the plan from the plain defaults of this target, planned and checked by the config
# stage's own functions (plan_board defaults), into the stage folder.
plan_package_defaults() {
    local plan="$PLAN_DIR/package_plan.sh"
    # The toolchain file plan_board writes (the Rockchip config stage names it OUTPUT_TOOLCHAIN_FILE, the template's
    # CMAKE_TOOLCHAIN)
    # shellcheck disable=SC2034
    OUTPUT_TOOLCHAIN_FILE="$PLAN_DIR/package_toolchain.cmake"
    # shellcheck disable=SC2034
    CMAKE_TOOLCHAIN="$PLAN_DIR/package_toolchain.cmake"
    plan_board defaults
    board_plan_lines > "$plan.tmp"
    check_plan_file "$plan.tmp" "${BOARD_PLAN_NAMES[@]}"
    mv -f "$plan.tmp" "$plan"
    # shellcheck source=/dev/null
    source "$plan"
}

validate_inputs() {
    local input_gguf="$1"
    local quant_method="$2"
    local model_name="$3"

    log_info "Validating target module inputs"

    if [[ ! -f "$input_gguf" ]]; then die "Input GGUF file not found: $input_gguf"; fi

    local file_size; file_size=$(wc -c < "$input_gguf" | tr -d ' ')
    if [[ "$file_size" -lt 1048576 ]]; then die "Input GGUF file suspiciously small"; fi

    if [[ -z "$quant_method" ]]; then die "Quantization method required"; fi
    if [[ -z "$model_name" ]]; then die "Model name required"; fi

    TARGET_CONFIG[INPUT_GGUF]="$input_gguf"
    TARGET_CONFIG[QUANT_METHOD]="$quant_method"
    TARGET_CONFIG[MODEL_NAME]="$model_name"
    TARGET_CONFIG[INPUT_SIZE_BYTES]="$file_size"
    TARGET_CONFIG[INPUT_SIZE_MB]="$((file_size / 1024 / 1024))"
    QUANTIZATION_STATS[OUTPUT_SIZE_MB]="$((file_size / 1024 / 1024))"

    log_success "Input validation completed"
}

# The framework's disk rule before the package is made (scripts/board_package.py room, the numbers and words of
# orchestrator/Core/disk_plan.py): the disk of the package folder must hold FILE... and still keep 2 GiB or 2 % of it
# free. The package copies the model next to the build's own; so the model is checked before anything slow (the
# functional test, the cross build) and every file again just before the copy. The framework's reservation ledger is
# in the per-user folder of the computer, which a build container does not see: the check counts the disk's free space.
package_room() {  # FILE...: stops the stage with the rule's plain reason when the package does not fit
    local problem
    problem="$(python3 "$PACKAGE_SCRIPTS/board_package.py" room "$OUTPUT_DIR/packages" \
        --what "the package for the board" "$@")" \
        || die "${problem:-The free space for the package could not be checked.} No package was made."
}

# The dependencies of the pinned llama.cpp that llmcf can fetch before the build, by the option that needs them
# (DEPENDENCY_OF_OPTION of recipe.py; tests/test_module_flags.py compares). The framework mounts a fetched copy
# read-only and names it in LLMCF_DEPS ("NAME=/mount ..."); only then is its option allowed.
DEPENDENCY_OPTIONS=(LLAMA_BUILD_BORINGSSL:BORINGSSL LLAMA_BUILD_LIBRESSL:LIBRESSL GGML_CPU_KLEIDIAI:KLEIDIAI)
# Valid CMake switches outside the allowlist (DECISION_SWITCHES of recipe.py; the person decides) and the ones never
# allowed in a module's flags (REFUSED_SWITCHES). tests/test_module_flags.py compares both lists.
DECISION_SWITCH_NAMES=(-G -T -A -U --fresh --log-level --log-context --debug-output --debug-find --trace
                       --trace-expand --warn-uninitialized --no-warn-unused-cli --check-system-vars
                       --compile-no-warning-as-error --install-prefix)
REFUSED_SWITCH_NAMES=(-C -P -S -B --preset --toolchain --build --install --open -E -N --graphviz --trace-redirect
                      --profiling-output --profiling-format --debugger --debugger-pipe)

# The CMake variables that make CMake or the build run another program (PROGRAM_VARIABLE_RE of recipe.py;
# tests/test_module_flags.py compares): a launcher, a code checker, the cross emulator, the build tool.
PROGRAM_VARIABLE_PATTERN='^CMAKE_([A-Za-z0-9]+_)?(COMPILER_LAUNCHER|LINKER_LAUNCHER|CLANG_TIDY|CPPCHECK|CPPLINT|INCLUDE_WHAT_YOU_USE|ICSTAT)$|^CMAKE_LINK_WHAT_YOU_USE_CHECK$|^CMAKE_CROSSCOMPILING_EMULATOR$|^CMAKE_MAKE_PROGRAM$'

prefetched_dependency() {  # NAME: the framework mounted its fetched copy for this build
    local entry
    for entry in ${LLMCF_DEPS:-}; do
        if [ "${entry%%=*}" = "$1" ] && [ -d "${entry#*=}" ]; then
            return 0
        fi
    done
    return 1
}

dependency_of_option() {  # OPTION: the dependency it needs, nothing when it needs none
    local entry
    for entry in "${DEPENDENCY_OPTIONS[@]}"; do
        if [ "${entry%%:*}" = "$1" ]; then
            echo "${entry#*:}"
            return
        fi
    done
}

# The verdict of each CMake option of WORD... (the plan's CMAKE_FLAGS, or the words of a cmake call), one line each:
# VERDICT, OPTION, REASON, KIND and WORDS, separated by the unit separator (octal 037, so empty fields stay fields):
# allowed, refused or decision; the option as cmake reads it ("-D NAME=VALUE" is -DNAME=VALUE, -G Ninja keeps its
# value); why it is refused or what it does; download or rule; the words it came from. The one verdict of recipe.py (cmake_flag_verdicts) in the build container: the same
# verdicts for the same words (tests/test_module_flags.py runs both over tests/data/cmake_flag_vectors.json).
# Contexts: --package (the default: the module's flags in the package's cross build, where the options of every
# build come after them and win), --alone (a cmake call of a module's own scripts: nothing comes after its words, and
# only what downloads or runs a script is refused) and --module (the strict check of the module's flags).
module_flag_verdicts() {  # [--package|--alone|--module] WORD...
    local context=package word words name value dependency key short reason
    case "${1:-}" in
        --alone) context=call; shift ;;
        --package) shift ;;
        --module) context=module; shift ;;
    esac
    while [ $# -gt 0 ]; do
        word="$1"
        words="$1"
        shift
        case "$word" in
            -D|-U|-G|-T|-A|-C|-P|-S|-B)
                if [ $# -gt 0 ]; then
                    words="$word $1"
                    if [ "$word" = -D ]; then
                        word="-D$1"
                    else
                        word="$word $1"
                    fi
                    shift
                fi ;;
        esac
        case "$word" in
            -D*)
                if [[ "$word" != *=* ]]; then
                    printf 'refused\037%s\037%s\037rule\037%s\n' "$word" "not -DNAME=VALUE" "$words"
                    continue
                fi
                name="${word#-D}"
                name="${name%%=*}"
                name="${name%%:*}"
                value="${word#*=}"
                dependency="$(dependency_of_option "$name")"
                if [ -n "$dependency" ] && prefetched_dependency "$dependency"; then
                    dependency=mounted
                fi
                if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_.+-]*$ ]]; then
                    printf 'refused\037%s\037%s\037rule\037%s\n' "$word" "not -DNAME=VALUE" "$words"
                elif [[ " ${NEVER_SET_OPTIONS[*]} " == *" $name "* ]] && [ "$dependency" != mounted ]; then
                    printf 'refused\037%s\037%s\037download\037%s\n' "$word" \
                        "$name downloads at configure time and is never set" "$words"
                elif [[ "$name" =~ $SCRIPT_VARIABLE_PATTERN ]]; then
                    printf 'refused\037%s\037%s\037download\037%s\n' "$word" \
                        "$name makes CMake run a CMake script, which can set any option" "$words"
                elif [ "$dependency" = mounted ]; then
                    printf 'allowed\037%s\037\037\037%s\n' "$word" "$words"
                elif [ "$context" != call ] && { [ "$name" = CMAKE_TOOLCHAIN_FILE ] || [ "$name" = CMAKE_MODULE_PATH ] \
                        || [[ "$name" =~ ^CMAKE_USER_MAKE_RULES_OVERRIDE(_[A-Za-z]+)?$ ]]; }; then
                    printf 'refused\037%s\037%s\037rule\037%s\n' "$word" "$name loads code or another toolchain" "$words"
                elif [ "$context" != call ] && [[ "$name" =~ $PROGRAM_VARIABLE_PATTERN ]]; then
                    printf 'refused\037%s\037%s\037rule\037%s\n' "$word" \
                        "$name makes CMake or the build run another program, which can change what is built" "$words"
                elif [ "$context" != call ] && [[ "$name" == FETCHCONTENT_* ]] \
                        && [ "$name" != FETCHCONTENT_FULLY_DISCONNECTED ]; then
                    printf 'refused\037%s\037%s\037rule\037%s\n' "$word" "$name is set only by llmcf's dependency cache" \
                        "$words"
                elif [ "$context" != call ] && [ "$name" = CMAKE_PREFIX_PATH ]; then
                    if container_paths "$value"; then
                        printf 'allowed\037%s\037\037\037%s\n' "$word" "$words"
                    else
                        printf 'refused\037%s\037%s\037rule\037%s\n' "$word" \
                            "CMAKE_PREFIX_PATH names only folders inside the build container (absolute paths)" "$words"
                    fi
                elif [ "$context" != package ] && [[ " ${DOWNLOAD_OFF_OPTIONS[*]} " == *" $name "* ]] \
                        && [ "$value" != OFF ]; then
                    printf 'refused\037%s\037%s\037download\037%s\n' "$word" \
                        "$name must be OFF on every build: it lets llama.cpp download" "$words"
                elif [ "$context" != package ] && { { [ "$name" = FETCHCONTENT_FULLY_DISCONNECTED ] \
                        && [ "$value" != ON ]; } || { [ "$name" = LLAMA_SUBPROCESS ] && [ "$value" != OFF ]; }; }; then
                    printf 'refused\037%s\037%s\037download\037%s\n' "$word" \
                        "$name must be $([ "$name" = LLAMA_SUBPROCESS ] && echo OFF || echo ON) on every build" "$words"
                else
                    printf 'allowed\037%s\037\037\037%s\n' "$word" "$words"
                fi
                continue ;;
            -C*|-P*)
                printf 'refused\037%s\037%s\037download\037%s\n' "${words%% *}" \
                    "CMake runs a script with -C or -P, and a script can set any option" "$words"
                continue ;;
        esac
        if [ "$context" = call ]; then
            printf 'allowed\037%s\037\037\037%s\n' "$word" "$words"
            continue
        fi
        if [[ "$word" == -W* ]]; then
            if [[ "$word" =~ ^-W(no-)?(error=)?(dev|deprecated)$ ]]; then
                printf 'allowed\037%s\037\037\037%s\n' "$word" "$words"
            else
                printf 'refused\037%s\037%s\037rule\037%s\n' "$word" \
                    "not a CMake warning option (a compiler warning belongs in the C flags)" "$words"
            fi
            continue
        fi
        key="${words%% *}"
        key="${key%%=*}"
        short=""
        if [[ "$key" != --* ]] && [ "${#key}" -gt 2 ]; then
            short="${key:0:2}"
        fi
        if [[ " ${REFUSED_SWITCH_NAMES[*]} " == *" $key "* ]] \
                || { [ -n "$short" ] && [[ " ${REFUSED_SWITCH_NAMES[*]} " == *" $short "* ]]; }; then
            reason="a switch llmcf never allows in a module's flags"
            printf 'refused\037%s\037%s\037rule\037%s\n' "$word" "$reason" "$words"
        elif [[ " ${DECISION_SWITCH_NAMES[*]} " == *" $key "* ]] \
                || { [ -n "$short" ] && [[ " ${DECISION_SWITCH_NAMES[*]} " == *" $short "* ]]; }; then
            printf 'decision\037%s\037%s\037\037%s\n' "$word" "a valid CMake option outside llmcf's list" "$words"
        else
            printf 'refused\037%s\037%s\037rule\037%s\n' "$word" "not a CMake option the framework knows" "$words"
        fi
    done
}

container_paths() {  # VALUE: every ';' part is an absolute folder inside the container
    local part found=0
    local IFS=';'
    for part in $1; do
        [ -n "$part" ] || continue
        found=1
        [[ "$part" =~ ^/[A-Za-z0-9._+/-]{0,255}$ ]] || return 1
        [[ "/$part/" != */../* ]] || return 1
    done
    [ "$found" = 1 ]
}

# The person's decision on a valid option outside the allowlist (DECISIONS round 25 Q6), as the framework hands it to
# the build: LLMCF_ALLOWED_FLAGS (the options the person allowed, as cmake reads them, separated by the unit separator) and
# LLMCF_FLAG_DECISION=standard (go on with the standard flags: such options are left out).
flag_allowed_by_person() {  # OPTION
    local allowed
    local IFS=$'\037'
    for allowed in ${LLMCF_ALLOWED_FLAGS:-}; do
        [ "$allowed" = "$1" ] && return 0
    done
    return 1
}

# The first of the module's CMake flags (FLAG...: the plan's CMAKE_FLAGS, word by word as the cross build passes them)
# that the cross build refuses, with the reason; nothing when there is none. The options of every build come after
# the module's flags and win (cross_compile_target), except an option of NEVER_SET_OPTIONS, which downloads whenever it
# is set, and a CMake script (-C, an initial cache, -P, or a project include: SCRIPT_VARIABLE_PATTERN), which can set
# any option. cmake reads "-D NAME=VALUE" as one option, and -DNAME:TYPE=VALUE names NAME.
# With --alone (every cmake call of a module's own scripts, build.sh's cmake): no option of every build comes after the
# words, so an option every build keeps OFF must be OFF there too, FETCHCONTENT_FULLY_DISCONNECTED ON and
# LLAMA_SUBPROCESS OFF (refused_option of recipe.py: the one rule). A valid option outside the allowlist that the person
# has not allowed (and not left out: LLMCF_FLAG_DECISION=standard) stops the package too. The verdicts of the CMake
# flags are all in module_flag_verdicts.
module_flag_problem() {  # [--alone] FLAG...
    local verdict option reason kind words
    while IFS=$'\037' read -r verdict option reason kind words; do
        case "$verdict" in
            refused)
                echo "$option ($reason)"
                return ;;
            decision)
                if ! flag_allowed_by_person "$option" && [ "${LLMCF_FLAG_DECISION:-}" != standard ]; then
                    echo "$option ($reason: the person decides in llmcf whether to allow it or to go on with the standard flags)"
                    return
                fi ;;
        esac
    done < <(module_flag_verdicts "$@")
}

# The kind of the first problem module_flag_problem names: download (it would let llama.cpp download or run a
# script) or rule (any other).
module_flag_problem_kind() {  # [--alone] FLAG...
    local verdict option reason kind words
    while IFS=$'\037' read -r verdict option reason kind words; do
        if [ "$verdict" = refused ]; then
            echo "$kind"
            return
        fi
        if [ "$verdict" = decision ] && ! flag_allowed_by_person "$option" \
                && [ "${LLMCF_FLAG_DECISION:-}" != standard ]; then
            echo rule
            return
        fi
    done < <(module_flag_verdicts "$@")
    echo rule
}

# The module's flags with the options the person left out (LLMCF_FLAG_DECISION=standard): every word of an option of
# the verdict "decision" that the person did not allow is dropped; the rest stays in its order.
module_flags_kept() {  # FLAG...
    local verdict option reason kind words kept=()
    while IFS=$'\037' read -r verdict option reason kind words; do
        if [ "$verdict" = decision ] && ! flag_allowed_by_person "$option" \
                && [ "${LLMCF_FLAG_DECISION:-}" = standard ]; then
            log_info "Left out $option (the standard flags, as the person decided)." >&2
            continue
        fi
        # shellcheck disable=SC2206 # the words of one option, split as the plan's words were
        kept+=($words)
    done < <(module_flag_verdicts "$@")
    echo "${kept[*]}"
}

check_module_flags() {  # before anything slow: the module's CMake flags in the plan, as the cross build passes them
    local problem flags=()
    read -r -a flags <<< "${CMAKE_FLAGS:-}"
    [ ${#flags[@]} -gt 0 ] || return 0
    problem="$(module_flag_problem "${flags[@]}")"
    if [ -n "$problem" ]; then
        if [ "$(module_flag_problem_kind "${flags[@]}")" = download ]; then
            die "The CMake flags of the build plan would let llama.cpp download while it is built: $problem; builds have no network: leave it out. No package was made."
        fi
        die "The CMake flags of the build plan are not allowed: $problem. No package was made."
    fi
    CMAKE_FLAGS="$(module_flags_kept "${flags[@]}")"
}

# A short run of the quantized model on this build machine: the convert stage's functional test (scripts/model_check.py
# test, the one implementation), with the image's own llama-completion (llama-cli when the image has only that): 8
# tokens, stopped when it prints nothing for FUNCTIONAL_TEST_SECONDS or the model repeats itself,
# offline, the options this tool has. A run that ends with an error or
# crashes on the model stops the package. A timeout, a stop from outside (the out-of-memory killer), a tool this
# machine cannot run or start, no tool in the image and an embedding model are notes, and the manifest says which.
# The package's test_model.sh runs the same kind of test on the board.
functional_test() {
    local model="$1" result_file="$SCRATCH_DIR/functional-test.result" result="" tool="" summary=""
    [ -f "$PACKAGE_SCRIPTS/model_check.py" ] || \
        die "The functional test of the package is missing: scripts/model_check.py is not in the image."
    rm -f "$result_file"
    python3 "$PACKAGE_SCRIPTS/model_check.py" test "$model" --tools "$LLAMA_BASE/build/bin" \
        --test-seconds "$FUNCTIONAL_TEST_SECONDS" --result "$result_file" --what "the quantized model" \
        --stage TARGET || exit 1
    { IFS= read -r result; IFS= read -r tool; IFS= read -r summary; } < "$result_file"
    rm -f "$result_file"
    TARGET_CONFIG[FUNCTIONAL_TEST]="$summary"
    case "$result" in
        "timed out")
            log_warn "The functional test printed nothing for ${FUNCTIONAL_TEST_SECONDS} s; the package is made anyway." ;;
        stopped) log_warn "The functional test was stopped (${summary:-${tool:-the tool}}); the package is made anyway." ;;
    esac
    log_success "Functional test: $summary"
}

cross_compile_target() {
    log_info "Schritt 1B: Cross-Kompilierung für Target (${PROFILE_ARCH:-unknown})..."

    # In the work folder: the image's llama.cpp source is read-only in a build container.
    local build_dir="$SCRATCH_DIR/build_target"
    rm -rf "$build_dir" && mkdir -p "$build_dir"
    cd "$build_dir"

    local cmake_args=()
    cmake_args+=("-DCMAKE_BUILD_TYPE=Release")
    cmake_args+=("-DBUILD_SHARED_LIBS=OFF")
    cmake_args+=("-DLLAMA_OPENSSL=OFF")
    cmake_args+=("-DLLAMA_BUILD_SERVER=ON")

    # WICHTIG: Setze die Toolchain (mit den Optimierungs-Flags) aus config_module.sh
    cmake_args+=("-DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}")
    local flags=()
    read -r -a flags <<< "${CMAKE_FLAGS:-}"
    if [ ${#flags[@]} -gt 0 ]; then
        cmake_args+=("${flags[@]}")
    fi
    # Nothing is downloaded at configure or build time (the build container has no network, a container started by
    # hand may have); only what the package needs. Every option of llama.cpp that can download something is off, as
    # in every llama.cpp build of the framework (DOWNLOAD_OPTIONS_OFF and DEPRECATED_DOWNLOAD_OPTIONS of
    # orchestrator/runtime/build/recipe.py, the image's Dockerfile): LLAMA_USE_PREBUILT_UI, on by default, would fetch
    # llama-server's web UI while it is built. llama-server has no subprocesses (LLAMA_SUBPROCESS=OFF, decision D22).
    # These come after the module's CMake flags, so they always win (CMake keeps the last value); the module generator
    # refuses such flags in a module (module_generator.download_flag_problems).
    cmake_args+=("-DLLAMA_CURL=OFF" "-DFETCHCONTENT_FULLY_DISCONNECTED=ON" "-DLLAMA_SUBPROCESS=OFF" "-DLLAMA_BUILD_TESTS=OFF")
    cmake_args+=("-DLLAMA_BUILD_EXAMPLES=OFF" "-DLLAMA_BUILD_APP=OFF" "-DLLAMA_USE_PREBUILT_UI=OFF" "-DLLAMA_BUILD_UI=OFF"
                 "-DLLAMA_LLGUIDANCE=OFF" "-DGGML_OPENMP_FETCH=OFF")
    if ! prefetched_dependency KLEIDIAI; then
        cmake_args+=("-DGGML_CPU_KLEIDIAI=OFF")
    fi
    # The dependencies llmcf fetched before the build, pinned and checked, outside the sandbox (DECISIONS round 25
    # Q6 addendum, orchestrator/runtime/build/deps.py): mounted read-only and named in LLMCF_DEPS. CMake takes each
    # from its folder (FETCHCONTENT_SOURCE_DIR_<NAME>) and, FETCHCONTENT_FULLY_DISCONNECTED=ON, fetches nothing.
    local entry
    for entry in ${LLMCF_DEPS:-}; do
        if prefetched_dependency "${entry%%=*}"; then
            cmake_args+=("-DFETCHCONTENT_SOURCE_DIR_${entry%%=*}=${entry#*=}")
        fi
    done

    log_info "Configuring target build..."
    # command cmake: the framework's own call (check_module_flags checked the module's flags of it before)
    if ! command cmake "$LLAMA_BASE" "${cmake_args[@]}"; then
        die "CMake configuration failed"
    fi

    local build_jobs="${BUILD_JOBS:-4}"
    log_info "Building target binaries (${build_jobs} jobs)..."

    local start_time=$SECONDS
    # Kompiliere CLI und Server
    if ! make -j"$build_jobs" llama-cli llama-server; then
        die "Target binary compilation failed."
    fi
    local build_time=$((SECONDS - start_time))

    # Pfade prüfen
    if [[ ! -f "$build_dir/bin/llama-cli" ]] || [[ ! -f "$build_dir/bin/llama-server" ]]; then
        die "Erforderliche Binaries (cli/server) wurden nicht erstellt."
    fi

    TARGET_CONFIG[LLAMA_CLI_BINARY]="$build_dir/bin/llama-cli"
    TARGET_CONFIG[LLAMA_SERVER_BINARY]="$build_dir/bin/llama-server"

    BUILD_STATS[BUILD_TIME]="$build_time"

    log_success "Target-Kompilierung abgeschlossen in ${build_time}s."
}

# The programs of the package are programs for the board's processor: their ELF header names it (elf_machine and
# elf_machine_of of config_module.sh).
check_board_binaries() {
    local key want got
    want="$(elf_machine_of "${PROFILE_ARCH:-}")"
    for key in LLAMA_CLI_BINARY LLAMA_SERVER_BINARY; do
        got="$(elf_machine "${TARGET_CONFIG[$key]}")"
        if [ -n "$want" ] && [ "$got" != "$want" ]; then
            die "$(basename "${TARGET_CONFIG[$key]}") is no program for the ${PROFILE_ARCH} board (ELF machine ${got:-none}, expected $want); no package was made."
        fi
        TARGET_CONFIG[${key}_MACHINE]="${got:-unknown}"
    done
    log_success "llama-cli and llama-server are programs for ${PROFILE_ARCH} (ELF machine ${TARGET_CONFIG[LLAMA_CLI_BINARY_MACHINE]})"
}

profile_text() {  # how the package was planned, for the model card and the manifest
    if [ "${PROFILE_SOURCE:-defaults}" = profile ]; then
        echo "the board's hardware profile (target_hardware_config.txt in this package)"
    else
        echo "none: the plain defaults of the ${PACKAGE_TARGET} target (no hardware profile was given)"
    fi
}

create_package_documentation() {
    local package_dir="$1"
    local readme_file="$package_dir/README.md"
    local model_file tag commit
    model_file="$(basename "${TARGET_CONFIG[INPUT_GGUF]}")"
    tag="$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)"
    commit="$(cat "$LLAMA_BASE/.llama_cpp_commit" 2>/dev/null || echo unknown)"

    log_info "Generiere Gold Standard Model Card..."

    cat > "$readme_file" << EOF
---
library_name: gguf
base_model: ${TARGET_CONFIG[MODEL_NAME]}
tags:
- ${PACKAGE_TARGET,,}
- ${PROFILE_ARCH:-unknown}
- quantization:${TARGET_CONFIG[QUANT_METHOD]}
- gguf
pipeline_tag: text-generation
---

# ${TARGET_CONFIG[MODEL_NAME]} (${TARGET_CONFIG[QUANT_METHOD]})

## 📦 Model Details
- **Base Model:** ${TARGET_CONFIG[MODEL_NAME]}
- **Format:** GGUF
- **Quantization:** ${TARGET_CONFIG[QUANT_METHOD]}
- **Target Architecture:** ${PROFILE_ARCH:-Generic}
- **CPU:** ${TARGET_CPU:-unknown}
- **Hardware profile:** $(profile_text)
- **File Size:** ${QUANTIZATION_STATS[OUTPUT_SIZE_MB]:-0} MB
- **Optimization Flags:** \`${CFLAGS:-none}\`

## 🚀 Quick Start (On ${PROFILE_ARCH:-the board})

### Test first
\`\`\`bash
./test_model.sh                      # checks the files (SHA256SUMS), then a short answer from the model
\`\`\`

### Option A: Direct CLI
\`\`\`bash
# Make binaries executable
chmod +x llama-cli llama-server

# Run inference (-st: one answer, then llama-cli ends)
./llama-cli -m ${model_file} -p "Hello, AI!" -n 128 -st
\`\`\`

### Option B: API Server
\`\`\`bash
# Start server on port 8080
./llama-server -m ${model_file} --host 0.0.0.0 --port 8080
\`\`\`

## Deploy to the board
- From your computer: \`./deploy.sh USER@HOST [DEST]\` copies this folder over scp (default DEST: llm/<this folder>
  in the user's home on the board), checks the copy there against SHA256SUMS and makes the programs executable. The
  board's host key must already be in your known_hosts; deploy.sh refuses an unknown or changed key
  (StrictHostKeyChecking=yes). Compare \`ssh-keyscan -t ed25519 HOST | ssh-keygen -lf -\` with the fingerprint the
  board shows at its own console (\`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub\`) before you add it.
- On the board itself: \`./deploy.sh [DEST]\` copies the package to DEST (default /opt/llm), as in version 1.x.
- Through the framework: its own deployment (llm-cli deploy, or the Deployment window) logs in to a board it knows
  and checks the board's key itself.

## Files and checks
- \`${model_file}\`: the model; \`llama-cli\`, \`llama-server\`: programs for ${PROFILE_ARCH:-the board}
- \`MANIFEST.json\`: every build detail (model, quantization, llama.cpp, flags, tests, each file with its size and
  sha256); \`SHA256SUMS\`: the checksums of every file (\`sha256sum -c SHA256SUMS\`)
- \`cmake_toolchain.cmake\`: the toolchain the programs were built with

## ⚠️ Requirements
- **Memory:** Approx. ${QUANTIZATION_STATS[OUTPUT_SIZE_MB]:-0} MB + 500MB Overhead
- **OS:** Linux (${PROFILE_ARCH:-aarch64})

## 🛠️ Build Info
Generated by LLM Cross-Compiler Framework.
- **Date:** $(date -Iseconds)
- **Build Time:** ${BUILD_STATS[BUILD_TIME]:-0}s
- **llama.cpp:** ${tag} (commit ${commit})
- **Tests in the build:** functional ${TARGET_CONFIG[FUNCTIONAL_TEST]:-not run}; programs checked for
  ${PROFILE_ARCH:-the board}; package checked against SHA256SUMS

EOF
}

create_helper_scripts() {
    local package_dir="$1" name source
    for name in test_model.sh deploy.sh; do
        source="$PACKAGE_SCRIPTS/package_$name"
        [ -f "$source" ] || die "The helper script scripts/package_$name of this target is missing; no package was made."
        cp "$source" "$package_dir/$name"
        chmod 755 "$package_dir/$name"
        bash -n "$package_dir/$name" || die "The helper script $name is not a valid shell script."
    done
}

# One number of the convert stage's statistics (convert_stats.json: seconds, input_bytes, output_bytes), 0 without them
conversion_stat() {
    python3 - "$CONVERT_STATS" "$1" <<'PY' 2>/dev/null || echo 0
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2])
except (OSError, ValueError):
    value = None
print(int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0)
PY
}

# The first line of a program's --version (never a function of this script), "unknown" without the program; a
# line that names a path is not kept (the manifest holds no path of the build machine)
tool_version() {
    local line=""
    if command -v "$1" >/dev/null 2>&1; then
        line="$(command "$1" --version 2>/dev/null | head -n 1 | tr -d '\r' | cut -c1-120)" || true
    fi
    case "$line" in
        ''|*/*) printf 'unknown' ;;
        *) printf '%s' "$line" ;;
    esac
}

# The C compiler of the board's cross build: the one the toolchain file of the build plan names, else cc
plan_compiler() {
    local compiler=""
    if [ -f "${CMAKE_TOOLCHAIN_FILE:-}" ]; then
        compiler="$(sed -n 's/^[Ss][Ee][Tt](CMAKE_C_COMPILER[[:space:]]\{1,\}\([^[:space:])]*\).*/\1/p' "$CMAKE_TOOLCHAIN_FILE" | head -n 1)"
    fi
    printf '%s' "${compiler:-cc}"
}

create_package_manifest() {
    local package_dir="$1" model_file imatrix="no"
    model_file="$(basename "${TARGET_CONFIG[INPUT_GGUF]}")"
    if [ "${USE_IMATRIX:-0}" = "1" ]; then
        imatrix="yes"
    fi
    python3 "$PACKAGE_SCRIPTS/board_package.py" write "$package_dir" \
        --set "name=${TARGET_CONFIG[PACKAGE_NAME]}" \
        --set "model=${TARGET_CONFIG[MODEL_NAME]}" \
        --set "quantization=${TARGET_CONFIG[QUANT_METHOD]}" \
        --set "format=gguf" \
        --set "target=${PACKAGE_TARGET}" \
        --set "board=${BOARD:-}" \
        --set "build.source_model=$(basename "${MODEL_SOURCE%/}")" \
        --set "build.input_format=${INPUT_FORMAT:-model}" \
        --set "build.importance_matrix=$imatrix" \
        --set "build.llama_cpp.tag=$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)" \
        --set "build.llama_cpp.commit=$(cat "$LLAMA_BASE/.llama_cpp_commit" 2>/dev/null || echo unknown)" \
        --int "build.cross_compile_seconds=${BUILD_STATS[BUILD_TIME]:-0}" \
        --int "build.convert_seconds=$(conversion_stat seconds)" \
        --int "build.quantize_seconds=${TARGET_CONFIG[QUANTIZE_SECONDS]:-0}" \
        --int "build.input_bytes=$(conversion_stat input_bytes)" \
        --int "build.converted_bytes=$(conversion_stat output_bytes)" \
        --set "build.tools.python=$(tool_version python3)" \
        --set "build.tools.cmake=$(tool_version cmake)" \
        --set "build.tools.compiler=$(tool_version "$(plan_compiler)")" \
        --int "build.jobs=${BUILD_JOBS:-4}" \
        --set "build.framework=LLM Cross-Compiler Framework" \
        --set "hardware.profile=$(profile_text)" \
        --set "hardware.source=${PROFILE_SOURCE:-defaults}" \
        --set "hardware.arch=${PROFILE_ARCH:-}" \
        --set "hardware.cpu=${TARGET_CPU:-generic}" \
        --set "hardware.cflags=${CFLAGS:-}" \
        --list "hardware.cpu_features.used=${CPU_FEATURES_USED:-}" \
        --features "hardware.cpu_features.unusable=${CPU_FEATURES_UNUSABLE:-}" \
        --set "hardware.cxxflags=${CXXFLAGS:-}" \
        --set "hardware.cmake_flags=${CMAKE_FLAGS:-}" \
        --set "hardware.neon=${HW_SUPPORTS_NEON:-unknown}" \
        --set "hardware.avx2=${HW_SUPPORTS_AVX2:-unknown}" \
        --set "hardware.avx512=${HW_SUPPORTS_AVX512:-unknown}" \
        --set "tests.inputs=passed: the model is a file of at least 1 MiB" \
        --set "tests.functional=${TARGET_CONFIG[FUNCTIONAL_TEST]:-not run}" \
        --set "tests.binaries=passed: ELF programs for ${PROFILE_ARCH:-the board} (machine ${TARGET_CONFIG[LLAMA_CLI_BINARY_MACHINE]:-unknown})" \
        --set "tests.package=SHA256SUMS of every file, checked after packaging (board_package.py check)" \
        --set "run.test=./test_model.sh [PROMPT] [TOKENS]" \
        --set "run.cli=./llama-cli -m ${model_file} -p \"Hello, AI!\" -n 128 -st" \
        --set "run.server=./llama-server -m ${model_file} --host 0.0.0.0 --port 8080" \
        --set "deploy.by_hand=./deploy.sh USER@HOST [DEST]: scp to the board, the host key must be known (StrictHostKeyChecking=yes)" \
        --set "deploy.on_the_board=./deploy.sh [DEST]: copies the package to DEST (default /opt/llm)" \
        --set "deploy.framework=llm-cli deploy, or the Deployment window: the framework's own board session" \
        --model "$model_file" \
        --binary "llama-cli=${TARGET_CONFIG[LLAMA_CLI_BINARY_MACHINE]:-unknown}" \
        --binary "llama-server=${TARGET_CONFIG[LLAMA_SERVER_BINARY_MACHINE]:-unknown}" \
        || die "The manifest of the package could not be written."
}

create_deployment_package() {
    local model_name="${TARGET_CONFIG[MODEL_NAME]}"
    local quant_method="${TARGET_CONFIG[QUANT_METHOD]}"
    local final_package_dir="$OUTPUT_DIR/packages"

    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local package_name="${model_name}_${quant_method,,}_${PROFILE_ARCH:-unknown}_${timestamp}"
    local package_dir="$final_package_dir/$package_name"

    log_info "Schritt 4: Erstelle Deployment-Paket: $package_name"

    local copies=("${TARGET_CONFIG[INPUT_GGUF]}" "${TARGET_CONFIG[LLAMA_CLI_BINARY]}"
                  "${TARGET_CONFIG[LLAMA_SERVER_BINARY]}" "${CMAKE_TOOLCHAIN_FILE}")
    if [[ -f "${HARDWARE_PROFILE:-}" ]]; then
        copies+=("$HARDWARE_PROFILE")
    fi
    package_room "${copies[@]}"

    rm -rf "$package_dir"
    mkdir -p "$package_dir"

    # Kopiere Artefakte
    cp "${TARGET_CONFIG[INPUT_GGUF]}" "$package_dir/"
    cp "${TARGET_CONFIG[LLAMA_CLI_BINARY]}" "$package_dir/llama-cli"
    cp "${TARGET_CONFIG[LLAMA_SERVER_BINARY]}" "$package_dir/llama-server"

    cp "${CMAKE_TOOLCHAIN_FILE}" "$package_dir/cmake_toolchain.cmake"
    if [[ -f "${HARDWARE_PROFILE:-}" ]]; then
        cp "$HARDWARE_PROFILE" "$package_dir/target_hardware_config.txt"
    fi

    TARGET_CONFIG[PACKAGE_DIR]="$package_dir"
    TARGET_CONFIG[PACKAGE_NAME]="$package_name"

    # Generiere Dokumentation & Helper; the manifest and SHA256SUMS last, over every file
    create_package_documentation "$package_dir"
    create_helper_scripts "$package_dir"
    create_package_manifest "$package_dir"
    python3 "$PACKAGE_SCRIPTS/board_package.py" check "$package_dir" \
        || die "The package does not match its SHA256SUMS or MANIFEST.json (above)."

    # Symlink
    local latest_link="$final_package_dir/${model_name}_${quant_method,,}_latest"
    rm -f "$latest_link"
    ln -s "$package_name" "$latest_link"

    log_success "Deployment package created at: $package_dir"
}

print_final_summary() {
    echo ""
    echo "=================================================="
    echo "       BUILD & QUANTIZATION COMPLETED"
    echo "=================================================="
    echo " Model:         ${TARGET_CONFIG[MODEL_NAME]}"
    echo " Quantization:  ${TARGET_CONFIG[QUANT_METHOD]}"
    echo " Target Arch:   ${PROFILE_ARCH:-unknown}"
    echo " Profile:       $(profile_text)"
    echo " Package:       ${TARGET_CONFIG[PACKAGE_DIR]}"
    echo " Model Card:    Generated (README.md)"
    echo " Manifest:      MANIFEST.json, SHA256SUMS (checked)"
    echo " Tests:         functional ${TARGET_CONFIG[FUNCTIONAL_TEST]:-not run}"
    echo "=================================================="
    echo ""
}

package_for_board() {
    local f
    if [ "${BOARD_PACKAGE:-0}" != "1" ]; then
        plan_package_defaults
    fi
    if [ -z "${TARGET_CONFIG[QUANTIZED_GGUF]:-}" ]; then
        for f in "$OUTPUT_DIR"/*.gguf "${CONVERT_OUTPUT:-}"; do
            if [ -f "$f" ]; then
                TARGET_CONFIG[QUANTIZED_GGUF]="$f"
                TARGET_CONFIG[QUANT_METHOD]="$(normalize_gguf_type "$QUANT")"
                break
            fi
        done
    fi
    validate_inputs "${TARGET_CONFIG[QUANTIZED_GGUF]:-}" "${TARGET_CONFIG[QUANT_METHOD]:-}" "$MODEL_NAME"
    package_room "${TARGET_CONFIG[INPUT_GGUF]}"
    check_module_flags
    functional_test "${TARGET_CONFIG[QUANTIZED_GGUF]}"
    cross_compile_target
    check_board_binaries
    create_deployment_package
    print_final_summary
}

# ============================================================================
# END OF PACKAGE
# ============================================================================

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    # shellcheck source=/dev/null
    source "$BUILD_PLAN"

    quantize_model
    write_build_info
    if [ "$FORMAT" = "gguf" ] && { [ "$BOARD_PACKAGE" = "1" ] || [ "${PACKAGE_REQUESTED:-0}" = "1" ]; }; then
        package_for_board
    elif [ "${PACKAGE_REQUESTED:-0}" = "1" ]; then
        log_info "No package for the board: a package holds a GGUF model with llama.cpp's programs; a $FORMAT model runs with the Rockchip runtime of the board."
    fi

    echo "=== Artifacts ==="
    ls -la "$OUTPUT_DIR"

    local f
    while IFS= read -r f; do
        stage_output "$f"
    done < <(find "$OUTPUT_DIR" -type f)
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the framework's stage runner (the runtime layer's
    # runner.sh: /llmcf in a build container, the repository's copy in a checkout), with the package for the board
    # (version 1.x: target_module.sh quantized and packaged).
    if [ -z "${STAGE_RUNNER:-}" ]; then
        layer="${LLMCF_LAYER:-/llmcf}"
        [ -f "$layer/runner.sh" ] || layer="$(dirname "${BASH_SOURCE[0]}")/../../../orchestrator/runtime_layer"
        LLMCF_MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" exec sh "$layer/runner.sh" --to target --package "$@"
    fi
    main "$@"
fi
