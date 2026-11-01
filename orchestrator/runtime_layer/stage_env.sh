# shellcheck shell=bash
# stage_env.sh - what every stage module finds (part of the framework's runtime layer; tools.sh says what that is)
#
# The stage runner starts every module as `bash MODULE` with BASH_ENV pointing here, so bash reads this file before
# the module (and again in every bash script the module starts: the same reach as an exported function). It runs as
# the stage account, without any capability: nothing here is trusted by the runner.

# The stage scripts' one call into the runner: `stage_output FILE` names a file the stage wrote. A resume runs the
# stage again when such a file is gone or has another size.
stage_output() { printf '%s\n' "$1" >> "$STAGE_DIR/$STAGE_NAME.outputs"; }

# Every cmake call of the stages' own scripts (a module's setup, conversion and packaging commands, and the bash
# scripts they start): the words cmake gets, as the shell expanded them (-DLLAMA_BUILD_${X}=ON included), go through
# the one verdict of the CMake flags (module_flag_problem --alone of target_module.sh) before cmake runs. The
# framework's own calls (the toolchain check, the package's cross build, checked by check_module_flags) run
# `command cmake`.
cmake() {
    local problem
    problem="$(bash -c 'source "$1" && shift && module_flag_problem --alone "$@"' _ \
        "$MODULES_DIR/target_module.sh" "$@")" || {
        echo "ERROR: The CMake flags of this cmake call could not be checked; cmake was not started." >&2
        return 1
    }
    if [ -n "$problem" ]; then
        echo "ERROR: A cmake call of this build would let llama.cpp download while it is built: $problem; builds have no network: leave it out. cmake was not started." >&2
        return 1
    fi
    # Nothing sets an option without a word on the command line: a configure run starts from a fresh cache (a
    # CMakeCache.txt left in the build folder could hold any option), and a CMAKE_TOOLCHAIN_FILE in the environment
    # (CMake reads it when the command line names none) is cleared unless the build plan set exactly that file.
    local fresh=() keep=() planned=""
    case "${1:-}" in
        --build|--install|--open|-E|-P|-N|-L*|--help*|--version|--system-information|--find-package|--workflow) ;;
        *)
            if command cmake --help 2>/dev/null | grep -q -- '--fresh'; then
                fresh=(--fresh)
            fi ;;
    esac
    if [ -n "${CMAKE_TOOLCHAIN_FILE+set}" ]; then
        if [ -f "${BUILD_PLAN:-}" ]; then
            # shellcheck source=/dev/null
            planned="$(unset CMAKE_TOOLCHAIN_FILE; source "$BUILD_PLAN" >/dev/null 2>&1; printf '%s' "${CMAKE_TOOLCHAIN_FILE:-}")"
        fi
        if [ -z "$planned" ] || [ "$planned" != "$CMAKE_TOOLCHAIN_FILE" ]; then
            keep=(-u CMAKE_TOOLCHAIN_FILE)
        fi
    fi
    env "${keep[@]}" cmake "${fresh[@]}" "$@"
}
