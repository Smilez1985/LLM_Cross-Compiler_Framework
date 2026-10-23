#!/bin/bash
# config_module.sh - stage 2 of the Rockchip build: the build plan (run by build.sh, the stage runner)
# Part of LLM Cross-Compiler Framework
#
# Image:  targets/Rockchip/dockerfile: Debian bookworm for the build host's architecture (x86_64).
# Target: Rockchip boards, aarch64 Linux (RK3566, RK3568: CPU; RK3576, RK3588: CPU and NPU).
# Stage:  reads the request from the environment, normalizes it (lower-case format and board, the GGUF type names
#         and their aliases) and refuses what this target cannot build before anything slow runs. Then it writes the
#         build plan, build_config.sh in the stage folder, which the convert and target stages read.
#         With a hardware profile (HARDWARE_CONFIG_FILE, or $BUILD_CACHE_DIR/target_hardware_config.txt: the file
#         scripts/hardware_probe.sh writes on the board) it also writes the CMake toolchain and the compiler flags
#         for that board into the plan, as the hardware agent of version 1.x did; the target stage then packages
#         llama-cli and llama-server for the board as well. A build of the BuildEngine has no profile: the board's
#         runtime is planned from the board's own probe by the runtime builder (orchestrator/runtime/build/recipe.py).
#         The toolchain is the one this image has: the compilers are looked up where the shell finds them (the
#         aarch64 cross compilers for a Rockchip board, the image's own for an x86_64 profile), and a profile of a
#         system this image has no compiler for (a 32-bit armv7 system) is refused with the reason.
# Validation (version 1.x: "Professional Validation - compiler tests, syntax checks"): before the plan is used, the
#         planned compilers build and link a small C and C++ program with the planned flags (a program for the build
#         host itself is run, one for the board must be an ELF program for the board's processor), CMake reads the
#         generated toolchain file, and build_config.sh must be a valid shell script that sets every name the later
#         stages read. A plan that fails a check is never written. The target stage checks the same way when it plans
#         a package without a profile (the plain defaults of this target, plan_board defaults).
#
# Inputs (environment, set by BuildEngine):
#   MODEL_SOURCE   local HF model directory inside the container (staged by the host); an ONNX file or folder (rknn);
#                  or a GGUF file (gguf: quantized by the target stage, nothing converted)
#   TARGET_FORMAT  gguf | rkllm | rknn                       (default: gguf)
#   TARGET_BOARD   rk3588 | rk3576 | rk3566 | ...            (default: rk3588)
#   QUANTIZATION   e.g. Q4_K_M, IQ4_XS, F16 (gguf) / w4a16, w8a8 (rkllm) / i8, fp16 (rknn)
#   JOB_TYPE       build | imatrix
#   USE_IMATRIX / IMATRIX_PATH, DATASET_PATH, MODEL_NAME, MODEL_REVISION, MAX_CONTEXT_LENGTH (optional)
#   OPTIMIZATION_LEVEL is set as well but not used: the Rockchip conversions have no matching setting.
# The image sets LLAMA_CPP_PATH, RKLLM_PYTHON and RKNN_PYTHON; the BuildEngine mounts $BUILD_CACHE_DIR/output (results)
# and $BUILD_SCRATCH_DIR (the work folder for intermediates; the image filesystem may be read-only).

set -euo pipefail

BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
WORK_DIR="$BUILD_CACHE_DIR"
OUTPUT_DIR="${WORK_DIR}/output"
IMATRIX_DIR="${WORK_DIR}/imatrix"
# Large intermediates (F16 GGUF) go to the mounted work folder; the image filesystem may be read-only.
SCRATCH_DIR="${BUILD_SCRATCH_DIR:-$WORK_DIR}"
PLAN_DIR="${STAGE_DIR:-$SCRATCH_DIR}"
BUILD_PLAN="$PLAN_DIR/build_config.sh"
OUTPUT_TOOLCHAIN_FILE="$PLAN_DIR/cross_compile_toolchain.cmake"

LLAMA_BASE="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"
CONVERT_SCRIPT="$LLAMA_BASE/convert_hf_to_gguf.py"
QUANTIZE_BIN="$LLAMA_BASE/build/bin/llama-quantize"
IMATRIX_BIN="$LLAMA_BASE/build/bin/llama-imatrix"

# Hardware-Konfiguration
declare -A HW_CONFIG

# Compiler-Konfiguration
declare -A COMPILER_CONFIG

# Where the board's part of the plan comes from: profile (a hardware profile) or defaults (plain_defaults)
PROFILE_SOURCE=""

die() { echo "ERROR: $*" >&2; exit 1; }

# ============================================================================
# LOGGING & ERROR HANDLING
# ============================================================================

log_info() { echo "ℹ️  [$(date '+%H:%M:%S')] [CONFIG] $1"; }
log_success() { echo "✅ [$(date '+%H:%M:%S')] [CONFIG] $1"; }
log_warn() { echo "⚠️  [$(date '+%H:%M:%S')] [CONFIG] $1"; }

# ============================================================================
# GGUF TYPES (llama.cpp; the capabilities in target.yml list the same ones)
# ============================================================================

# Used by convert_module.sh, which sources this file.
# shellcheck disable=SC2034
{
# Output types convert_hf_to_gguf.py writes directly.
GGUF_CONVERT_TYPES=" f32 f16 bf16 q8_0 auto "
# Every quantization method of llama-quantize at the pin (QUANT_OPTIONS of tools/quantize/quantize.cpp, llama.cpp
# b11429 = v0.6.0), except F16, BF16 and F32, which the converter writes itself, and COPY, which quantizes nothing;
# its aliases Q3_K, Q4_K and Q5_K are accepted by normalize_gguf_type.
GGUF_QUANT_TYPES=" Q4_0 Q4_1 Q5_0 Q5_1 Q8_0 Q2_K Q2_K_S Q3_K_S Q3_K_M Q3_K_L Q4_K_S Q4_K_M Q5_K_S Q5_K_M Q6_K \
IQ1_S IQ1_M IQ2_XXS IQ2_XS IQ2_S IQ2_M IQ3_XXS IQ3_XS IQ3_S IQ3_M IQ4_NL IQ4_XS TQ1_0 TQ2_0 MXFP4_MOE Q1_0 Q2_0 "
# llama-quantize refuses these without an importance matrix: tensors of the file get a type that requires one
# (tensor_requires_imatrix of src/llama-quant.cpp at the pin: IQ1_S, IQ1_M, IQ2_XXS, IQ2_XS, IQ2_S, IQ3_XXS, and Q2_K in
# a Q2_K_S file; IQ2_M is mostly IQ2_S, and IQ3_XS makes attn_q, attn_k and the middle ffn_gate and ffn_up IQ3_XXS).
# The other i-quants lose quality without one.
GGUF_NEEDS_IMATRIX=" IQ1_S IQ1_M IQ2_XXS IQ2_XS IQ2_S IQ2_M IQ3_XXS IQ3_XS Q2_K_S "
GGUF_WANTS_IMATRIX=" IQ3_S IQ3_M IQ4_NL IQ4_XS "
}

normalize_gguf_type() {
    local q
    q="$(echo "$1" | tr '[:lower:]' '[:upper:]')"
    case "$q" in
        ""|F16|FP16) echo "F16" ;;
        BF16) echo "BF16" ;;
        F32|FP32) echo "F32" ;;
        INT8|Q8) echo "Q8_0" ;;
        INT4|Q4|Q4_K) echo "Q4_K_M" ;;
        Q5|Q5_K) echo "Q5_K_M" ;;
        Q3_K) echo "Q3_K_M" ;;
        *) echo "$q" ;;
    esac
}

# ============================================================================
# THE REQUEST (the checks of the former build.sh dispatcher)
# ============================================================================

plan_request() {
    FORMAT="$(echo "${TARGET_FORMAT:-gguf}" | tr '[:upper:]' '[:lower:]')"
    BOARD="$(echo "${TARGET_BOARD:-rk3588}" | tr '[:upper:]' '[:lower:]')"
    QUANT="${QUANTIZATION:-}"
    JOB="${JOB_TYPE:-build}"
    MODEL_SOURCE="${MODEL_SOURCE:-}"
    # The input: a model for the converters (a Hugging Face folder; RKNN: an ONNX file or folder), or a GGUF file,
    # the F16 GGUF that target_module.sh of version 1.x took (the convert stage then converts nothing).
    INPUT_FORMAT="model"
    if [ -f "$MODEL_SOURCE" ] && [[ "$MODEL_SOURCE" == *.[gG][gG][uU][fF] ]]; then
        INPUT_FORMAT="gguf"
    fi
    if [ "$INPUT_FORMAT" = "gguf" ]; then
        # The defaults of target_module.sh of version 1.x: the file name without .gguf and .fp16, and Q4_K_M
        local name
        name="$(basename "$MODEL_SOURCE")"
        name="${name%.*}"
        MODEL_NAME="${MODEL_NAME:-${name%.fp16}}"
        if [ "$FORMAT" = "gguf" ] && [ -z "$QUANT" ]; then
            QUANT="Q4_K_M"
        fi
    else
        MODEL_NAME="${MODEL_NAME:-$(basename "${MODEL_SOURCE%/}")}"
    fi

    echo "Model:        $MODEL_SOURCE"
    echo "Format:       $FORMAT"
    echo "Board:        $BOARD"
    echo "Quantization: ${QUANT:-<default>}"
    echo "Job:          $JOB"

    if [ -z "$MODEL_SOURCE" ] && [ "${STAGE_LAST:-}" = "config" ]; then
        # config_module.sh --hardware-config FILE (version 1.x): the toolchain and the plan of the board only
        echo "No model (--input): the build plan holds the hardware profile only."
    else
        [ -n "$MODEL_SOURCE" ] && [ -e "$MODEL_SOURCE" ] || die "MODEL_SOURCE '$MODEL_SOURCE' does not exist in the container."
    fi

    # The importance-matrix job converts and measures only; its container has no output folder.
    [ "$JOB" = "imatrix" ] && return 0

    mkdir -p "$OUTPUT_DIR"
    case "$FORMAT" in
        gguf|rknn) ;;
        rkllm)
            case "$BOARD" in
                *rk3588*|*rk3576*) ;;
                *) die "RKLLM needs an RK3588 or RK3576 NPU; board '$BOARD' should use the gguf format (CPU)." ;;
            esac
            ;;
        *)
            die "Format '$FORMAT' is not supported by the Rockchip target (gguf, rkllm, rknn)."
            ;;
    esac
}

# ============================================================================
# HARDWARE CONFIG PARSING (hardware profile of the board, optional)
# ============================================================================

load_hardware_config() {
    log_info "Lade Hardware-Konfiguration: $HARDWARE_CONFIG_FILE"

    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        key=$(echo "$key" | tr -d ' \t')
        value=$(echo "$value" | tr -d ' \t\r' | sed 's/^"//;s/"$//')
        HW_CONFIG["$key"]="$value"
    done < <(grep -E '^[^#]*=' "$HARDWARE_CONFIG_FILE")

    # The hardware probe (scripts/hardware_probe.sh, .py and .ps1; configs/hardware_probe_schema.yml) names the system
    # ARCH and its cores CPU_MODEL ("Cortex-A55", "Cortex-A55+Cortex-A76"); the names of version 1.x's own files
    # (ARCHITECTURE_FULL, CPU_MODEL_NAME) still come first.
    if [[ -z "${HW_CONFIG[ARCHITECTURE_FULL]:-}" && -n "${HW_CONFIG[ARCH]:-}" ]]; then
        case "${HW_CONFIG[ARCH]}" in
            arm64|aarch64) HW_CONFIG[ARCHITECTURE_FULL]="aarch64" ;;
            *) HW_CONFIG[ARCHITECTURE_FULL]="${HW_CONFIG[ARCH]}" ;;
        esac
    fi
    HW_CONFIG[CPU_MODEL_NAME]="${HW_CONFIG[CPU_MODEL_NAME]:-${HW_CONFIG[CPU_MODEL]:-}}"
    HW_CONFIG[ARCHITECTURE_FULL]="${HW_CONFIG[ARCHITECTURE_FULL]:-aarch64}"
    # The build jobs are capped at the board's cores, as in version 1.x (CPU_CORES of its own files); the probe names
    # them CPU_CORES_LOGICAL.
    HW_CONFIG[CPU_CORES]="${HW_CONFIG[CPU_CORES]:-${HW_CONFIG[CPU_CORES_LOGICAL]:-}}"
    check_profile_system
    PROFILE_SOURCE="profile"
    log_info "Hardware-Konfiguration geladen (${HW_CONFIG[CPU_MODEL_NAME]:-Unknown})"
}

# This image has the aarch64 cross compilers (the Rockchip boards) and the host's own: programs for any other system
# (a 32-bit OS on a board writes ARCH=armv7) could not run there.
check_profile_system() {
    local arch="${HW_CONFIG[ARCHITECTURE_FULL]}"
    local refused="The hardware profile is of a $arch system; this image compiles for aarch64 (the Rockchip boards) and x86_64 only, so nothing was planned for it."
    case "$arch" in
        aarch64|x86_64) ;;
        armv7*|armv6*|armv8l|armhf|armel|arm)  # armv8l: `uname -m` of a 32-bit system on a 64-bit CPU (1.x probe)
            die "$refused The board runs a 32-bit system: the Rockchip boards have 64-bit CPUs (Cortex-A55, A72, A76), and this image has only the aarch64 cross compilers. Install the board's 64-bit (arm64) system, probe the board again (scripts/hardware_probe.sh) and use the new file." ;;
        *) die "$refused" ;;
    esac
}

# Without a hardware profile, a package for the board (build.sh --package; target_module.sh started by hand) is planned
# with the plain defaults of this target: the baseline every Rockchip board of it runs, aarch64 with generic ARMv8-A
# flags and NEON (every ARMv8-A core has it). The package's manifest and model card say so.
load_plain_defaults() {
    HW_CONFIG=([ARCHITECTURE_FULL]=aarch64 [CPU_MODEL_NAME]="" [SUPPORTS_NEON]=ON)
    PROFILE_SOURCE="defaults"
    log_info "No hardware profile: the plain defaults of this target (aarch64, generic ARMv8-A, NEON)"
}

# ============================================================================
# THE CPU OF AN x86_64 PROFILE: -march=native only for this build machine itself
# ============================================================================

# The x86 features of a hardware profile as the build recipe plans them for another machine
# (orchestrator/runtime/build/recipe.py: X86_FEATURES, _X86_NEEDS and _X86_FLAGS; tests/test_target_stage.py compares
# the plans): the probe's key, the ggml option it switches, the feature it builds on (a feature without it is not
# used) and its /proc/cpuinfo flag (configs/hardware_probe_schema.yml), which a probe file of version 1.x without
# the key still names in CPU_FLAGS_ALL.
X86_FEATURE_TABLE=(
    "SUPPORTS_SSE42 GGML_SSE42 - sse4_2"
    "SUPPORTS_AVX GGML_AVX - avx"
    "SUPPORTS_AVX2 GGML_AVX2 SUPPORTS_AVX avx2"
    "SUPPORTS_BMI2 GGML_BMI2 - bmi2"
    "SUPPORTS_F16C GGML_F16C SUPPORTS_AVX f16c"
    "SUPPORTS_FMA GGML_FMA SUPPORTS_AVX fma"
    "SUPPORTS_AVX512 GGML_AVX512 SUPPORTS_AVX2 avx512f"
    "SUPPORTS_AVX512_VBMI GGML_AVX512_VBMI SUPPORTS_AVX512 avx512vbmi"
    "SUPPORTS_AVX512_VNNI GGML_AVX512_VNNI SUPPORTS_AVX512 avx512_vnni"
    "SUPPORTS_AVX512_BF16 GGML_AVX512_BF16 SUPPORTS_AVX512 avx512_bf16"
    "SUPPORTS_AVX_VNNI GGML_AVX_VNNI SUPPORTS_AVX2 avx_vnni"
    "SUPPORTS_AMX_TILE GGML_AMX_TILE - amx_tile"
    "SUPPORTS_AMX_INT8 GGML_AMX_INT8 SUPPORTS_AMX_TILE amx_int8"
    "SUPPORTS_AMX_BF16 GGML_AMX_BF16 SUPPORTS_AMX_TILE amx_bf16"
)
# The compiler flags ggml adds for each option without GGML_NATIVE, in ggml's order
# (ggml/src/ggml-cpu/CMakeLists.txt:347-410 at the pin).
X86_FLAG_TABLE=(
    "GGML_SSE42 -msse4.2" "GGML_F16C -mf16c" "GGML_FMA -mfma" "GGML_BMI2 -mbmi2" "GGML_AVX -mavx" "GGML_AVX2 -mavx2"
    "GGML_AVX_VNNI -mavxvnni" "GGML_AVX512 -mavx512f -mavx512cd -mavx512vl -mavx512dq -mavx512bw"
    "GGML_AVX512_VBMI -mavx512vbmi" "GGML_AVX512_VNNI -mavx512vnni" "GGML_AVX512_BF16 -mavx512bf16"
    "GGML_AMX_TILE -mamx-tile" "GGML_AMX_INT8 -mamx-int8" "GGML_AMX_BF16 -mamx-bf16"
)

cpu_flag_list() {  # stdin: CPU flags (separated by spaces or commas) -> the sorted set, comma-separated, lower case
    tr 'A-Z,' 'a-z ' | tr -s ' \t' '\n' | sed '/^$/d' | sort -u | paste -sd, -
}

profile_cpu_flags() {  # CPU_FLAGS_ALL of the profile file as written (the loader drops the spaces of version 1.x)
    awk '/^[[:space:]]*CPU_FLAGS_ALL[[:space:]]*=/ { sub(/^[^=]*=/, ""); print; exit }' "$HARDWARE_CONFIG_FILE" \
        | tr -d '"\r'
}

# The hardware profile describes this build machine itself when it names this machine's CPU: the same system
# (uname -m), the same CPU model and the same CPU flags as /proc/cpuinfo here (the probe's ARCH, CPU_MODEL and
# CPU_FLAGS_ALL; spaces are not compared). Only then is -march=native the profile's CPU. A profile of another
# machine, or one without these facts, gets the explicit features of its probe (explicit_x86_flags).
profile_is_this_machine() {
    local here_model here_flags there_flags
    [ "$(uname -m)" = "${HW_CONFIG[ARCHITECTURE_FULL]}" ] && [ -r /proc/cpuinfo ] || return 1
    here_model="$(awk -F: '/^model name/ { print $2; exit }' /proc/cpuinfo | tr -d ' \t')"
    here_flags="$(awk -F: '/^flags/ { print $2; exit }' /proc/cpuinfo | cpu_flag_list)"
    there_flags="$(profile_cpu_flags | cpu_flag_list)"
    [ -n "$here_flags" ] && [ "$here_flags" = "$there_flags" ] \
        && [ -n "$here_model" ] && [ "$here_model" = "${HW_CONFIG[CPU_MODEL_NAME]:-}" ]
}

# X86_CFLAGS and X86_CMAKE_FLAGS from the profile's x86 features, as the build recipe plans a build for another
# machine: all 14 ggml options explicit (a feature the probe did not confirm is OFF, never more than it confirmed), a
# feature without the one it builds on is not used, and the compiler flags of the options that are ON on the x86-64
# baseline, whatever the image's compiler would choose by default.
explicit_x86_flags() {
    local row key option needs token value cpu_flags options=() unknown=()
    declare -A use
    cpu_flags="$(profile_cpu_flags | cpu_flag_list)"
    for row in "${X86_FEATURE_TABLE[@]}"; do
        read -r key option needs token <<< "$row"
        value="${HW_CONFIG[$key]:-}"
        if [ "$value" != ON ] && [ "$value" != OFF ]; then
            if [ -n "$cpu_flags" ]; then
                value=OFF
                if [[ ",$cpu_flags," == *",$token,"* ]]; then
                    value=ON
                fi
            else
                unknown+=("${key#SUPPORTS_}")
                value=OFF
            fi
        fi
        if [ "$value" = ON ] && [ "$needs" != - ] && [ "${use[$needs]:-OFF}" != ON ]; then
            log_warn "$key is ON without $needs in the hardware profile; not used"
            value=OFF
        fi
        use[$key]="$value"
        options+=("-D$option=$value")
    done
    if [ ${#unknown[@]} -gt 0 ]; then
        log_warn "The hardware profile does not say whether the CPU has ${unknown[*]}: left out (only what the probe confirmed is used)"
    fi
    X86_CMAKE_FLAGS="${options[*]}"
    local cflags="-march=x86-64"
    for row in "${X86_FLAG_TABLE[@]}"; do
        option="${row%% *}"
        if [[ " $X86_CMAKE_FLAGS " == *" -D$option=ON "* ]]; then
            cflags="$cflags ${row#* }"
        fi
    done
    X86_CFLAGS="$cflags"
}

# ============================================================================
# COMPILER FLAGS GENERATION
# ============================================================================

generate_compiler_flags() {
    log_info "Generiere Compiler-Flags für ${HW_CONFIG[ARCHITECTURE_FULL]}"

    local cflags="-O3 -pthread -fPIC"
    local cxxflags="-std=c++17"
    local cmake_flags="-DGGML_NATIVE=OFF"
    local build_jobs
    build_jobs=$(nproc)

    # --- HARDWARE-SPEZIFISCHE LOGIK ---

    # AArch64 (Rockchip/Jetson)
    if [[ "${HW_CONFIG[ARCHITECTURE_FULL]}" == "aarch64" ]]; then
        COMPILER_CONFIG[CROSS_PREFIX]="aarch64-linux-gnu"

        if [[ "${HW_CONFIG[CPU_MODEL_NAME]:-}" =~ "Cortex-A55" ]]; then
            log_info "Optimiere für Cortex-A55"
            cflags="$cflags -mcpu=cortex-a55 -march=armv8-a"
        elif [[ "${HW_CONFIG[CPU_MODEL_NAME]:-}" =~ "Cortex-A76" ]]; then
            log_info "Optimiere für Cortex-A76"
            cflags="$cflags -mcpu=cortex-a76 -march=armv8-a"
        elif [[ "${HW_CONFIG[CPU_MODEL_NAME]:-}" =~ "Cortex-A72" ]]; then
            log_info "Optimiere für Cortex-A72"
            cflags="$cflags -mcpu=cortex-a72 -march=armv8-a"
        else
            log_info "Verwende generisches ARMv8-A"
            cflags="$cflags -march=armv8-a"
        fi

        if [[ "${HW_CONFIG[SUPPORTS_NEON]:-OFF}" == "ON" ]]; then
            cmake_flags="$cmake_flags -DGGML_NEON=ON"
        fi
    fi

    # x86_64 (a PC): -march=native only when the profile is of this build machine itself (version 1.x: "Native
    # x86"); a profile of another PC gets its own CPU features, as the build recipe plans them (explicit_x86_flags).
    if [[ "${HW_CONFIG[ARCHITECTURE_FULL]}" == "x86_64" ]]; then
        if profile_is_this_machine; then
            log_info "The hardware profile is of this build machine: -march=native"
            cflags="$cflags -march=native"
            if [[ "${HW_CONFIG[SUPPORTS_AVX2]:-OFF}" == "ON" ]]; then
                cmake_flags="$cmake_flags -DGGML_AVX2=ON"
            fi
            if [[ "${HW_CONFIG[SUPPORTS_AVX512]:-OFF}" == "ON" ]]; then
                cmake_flags="$cmake_flags -DGGML_AVX512=ON"
            fi
        else
            explicit_x86_flags
            log_info "The hardware profile is of another machine: its CPU features ($X86_CFLAGS), not this machine's (-march=native)"
            cflags="$cflags $X86_CFLAGS"
            cmake_flags="$cmake_flags $X86_CMAKE_FLAGS"
        fi
    fi

    # Build-Jobs basierend auf Kernen (a number of cores, never a text the shell would read as a name)
    if [[ "${HW_CONFIG[CPU_CORES]:-}" =~ ^[1-9][0-9]*$ ]] && [[ "${HW_CONFIG[CPU_CORES]}" -lt "$build_jobs" ]]; then
        build_jobs=${HW_CONFIG[CPU_CORES]}
    fi

    # Speichere Konfiguration
    COMPILER_CONFIG[CFLAGS]="$cflags"
    COMPILER_CONFIG[CXXFLAGS]="$cxxflags $cflags"
    COMPILER_CONFIG[CMAKE_FLAGS]="$cmake_flags"
    COMPILER_CONFIG[BUILD_JOBS]="$build_jobs"
}

# ============================================================================
# THE TOOLCHAIN OF THIS IMAGE (version 1.x: "Cross-Compilation - automatic toolchain detection")
# ============================================================================

# The tools of the planned system, where the shell finds them: <prefix>-gcc, -g++, -ar and -strip of the cross
# toolchain for a board, the image's own gcc and g++ for a native (x86_64) plan, as the toolchain file of version 1.x
# named them.
find_compilers() {
    local prefix="${COMPILER_CONFIG[CROSS_PREFIX]:-}" key name path
    for key in CC CXX AR STRIP; do
        case "$key" in
            CC) name="gcc" ;;
            CXX) name="g++" ;;
            AR) name="ar" ;;
            *) name="strip" ;;
        esac
        if [ -n "$prefix" ]; then
            name="$prefix-$name"
        elif [ "$key" = AR ] || [ "$key" = STRIP ]; then
            continue
        fi
        path="$(command -v "$name" || true)"
        [[ "$path" == /* ]] || die "This image has no $name, which the plan for the ${HW_CONFIG[ARCHITECTURE_FULL]} system of the hardware profile needs; nothing was planned."
        [[ "$path" =~ ^[A-Za-z0-9._/+-]+$ ]] || die "The path of $name has characters a CMake toolchain file cannot hold unquoted; nothing was planned."
        COMPILER_CONFIG[$key]="$path"
    done
}

# ============================================================================
# VALIDATION (version 1.x: "Professional Validation - compiler tests, syntax checks")
# The same functions in targets/Rockchip and targets/_template (tests/test_target_stage.py compares them). They read
# the plan's compilers and flags from COMPILER_CONFIG (CC, CXX, CFLAGS, CXXFLAGS) and its system from
# HW_CONFIG[ARCHITECTURE_FULL].
# ============================================================================

# The ELF machine number of a program file (62 x86_64, 183 aarch64, 40 32-bit Arm, 3 32-bit x86, 243 RISC-V), empty for a
# file that is no ELF program.
elf_machine() {
    local -a head
    read -r -a head <<< "$(od -An -tu1 -N20 "$1" 2>/dev/null | tr '\n' ' ')"
    [ "${#head[@]}" -eq 20 ] && [ "${head[0]}" = 127 ] && [ "${head[1]}" = 69 ] && [ "${head[2]}" = 76 ] \
        && [ "${head[3]}" = 70 ] || return 0
    if [ "${head[5]}" = 2 ]; then
        echo $(( 256 * head[18] + head[19] ))  # big-endian
    else
        echo $(( head[18] + 256 * head[19] ))
    fi
}

elf_machine_of() {  # SYSTEM: the ELF machine number of its programs, empty when this script does not know it
    case "$1" in
        aarch64|arm64) echo 183 ;;
        x86_64|amd64) echo 62 ;;
        armv7*|armv6*|armv8l|armhf|armel|arm) echo 40 ;;
        x86|i386|i486|i586|i686) echo 3 ;;
        riscv64) echo 243 ;;
        *) echo "" ;;
    esac
}

run_compiler() {  # COMPILER OUTPUT ARGUMENTS...: builds OUTPUT, or stops the stage with the compiler's own words
    local compiler="$1" output="$2"
    shift 2
    if ! "$compiler" "$@" -o "$output" > "$output.log" 2>&1; then
        die "The compiler test failed: $(basename "$compiler") cannot build and link a small program with the flags of the plan (${COMPILER_CONFIG[CFLAGS]}): $(tail -n 8 "$output.log" | tr '\n' ' ')"
    fi
}

# The planned compilers build and link a small C and a small C++ program (with a thread) with the planned flags. A
# program built for this machine itself (-march=native on a machine of the plan's system) is run; any other must be an
# ELF program for the processor of the plan's system.
compiler_test() {
    local dir="$PLAN_DIR/compiler-test" arch="${HW_CONFIG[ARCHITECTURE_FULL]}" want got program
    local -a cflags cxxflags
    read -r -a cflags <<< "${COMPILER_CONFIG[CFLAGS]}"
    read -r -a cxxflags <<< "${COMPILER_CONFIG[CXXFLAGS]}"
    rm -rf "$dir" && mkdir -p "$dir"
    cat > "$dir/test.c" << 'EOF'
#include <stdio.h>
int main(void) { volatile float x = 1.5f; printf("compiler test %d\n", (int)(x * 2.0f)); return 0; }
EOF
    cat > "$dir/test.cpp" << 'EOF'
#include <thread>
#include <vector>
int main() { std::vector<int> v(4, 1); std::thread t([&v] { v[0] = 2; }); t.join(); return v[0] == 2 ? 0 : 1; }
EOF
    run_compiler "${COMPILER_CONFIG[CC]}" "$dir/test-c" "${cflags[@]}" "$dir/test.c"
    run_compiler "${COMPILER_CONFIG[CXX]}" "$dir/test-cxx" "${cxxflags[@]}" "$dir/test.cpp"
    want="$(elf_machine_of "$arch")"
    for program in "$dir/test-c" "$dir/test-cxx"; do
        if [[ " ${cflags[*]} " == *" -march=native "* ]] && [ "$(uname -m)" = "$arch" ]; then
            "$program" > "$program.run" 2>&1 \
                || die "The compiler test failed: a program the plan's compilers built for this machine does not run: $(tail -n 5 "$program.run" | tr '\n' ' ')"
        elif [ -n "$want" ]; then
            got="$(elf_machine "$program")"
            [ -n "$got" ] && [ "$got" = "$want" ] \
                || die "The compiler test failed: the plan's compilers do not build programs for the $arch system of the plan (ELF machine ${got:-none}, expected $want). The image needs a cross compiler for it (a module names it with CC and CXX in its Dockerfile)."
        else
            log_warn "The compiler test built and linked the programs; for a $arch system it cannot tell their processor."
        fi
    done
    rm -rf "$dir"
    log_success "Compiler test passed: C and C++ programs for $arch, built and linked with the flags of the plan"
}

# CMake reads the toolchain file as the target stage's cmake will (script mode stops at a syntax error), and the file
# names the system and the compilers.
check_toolchain_file() {
    local file="$1" key
    if command -v cmake > /dev/null 2>&1; then
        cmake -P "$file" > "$file.log" 2>&1 \
            || die "The CMake toolchain file is not valid: $(tail -n 5 "$file.log" | tr '\n' ' ')"
        rm -f "$file.log"
    fi
    for key in CMAKE_SYSTEM_NAME CMAKE_SYSTEM_PROCESSOR CMAKE_C_COMPILER CMAKE_CXX_COMPILER; do
        grep -Eq "^SET\\(${key}[[:space:]]" "$file" || die "The CMake toolchain file sets no $key."
    done
}

# A plan file is a valid shell script, and a clean shell that reads it has every name given after it.
check_plan_file() {  # FILE NAME...
    local file="$1" missing
    shift
    bash -n "$file" 2> "$file.log" || die "The build plan is not a valid shell script: $(tail -n 3 "$file.log" | tr '\n' ' ')"
    rm -f "$file.log"
    # shellcheck disable=SC2016  # expanded by the clean shell
    missing="$(env -i bash --noprofile --norc -c 'set -u; source "$1"; shift; for n in "$@"; do [ -n "${!n+set}" ] || printf "%s " "$n"; done' _ "$file" "$@" 2>&1)" \
        || die "The build plan cannot be read: $missing"
    [ -z "$missing" ] || die "The build plan does not set: ${missing% }."
}

# ============================================================================
# END OF VALIDATION
# ============================================================================

# The names the later stages read from the build plan (and, for a package, from its board part).
PLAN_NAMES=(FORMAT BOARD QUANT JOB MODEL_SOURCE INPUT_FORMAT MODEL_NAME OUTPUT_DIR IMATRIX_DIR SCRATCH_DIR LLAMA_BASE
            CONVERT_SCRIPT QUANTIZE_BIN IMATRIX_BIN BOARD_PACKAGE)
BOARD_PLAN_NAMES=(BOARD_PACKAGE PROFILE_SOURCE HARDWARE_PROFILE TARGET_CPU PROFILE_ARCH CFLAGS CXXFLAGS CMAKE_FLAGS
                  CMAKE_TOOLCHAIN_FILE BUILD_JOBS)

# ============================================================================
# CMAKE TOOLCHAIN GENERATION
# ============================================================================

generate_cmake_toolchain() {
    log_info "Generiere CMake Toolchain: $OUTPUT_TOOLCHAIN_FILE"

    # CCACHE Detection (Im Container)
    local ccache_path
    ccache_path=$(command -v ccache || echo "")

    cat > "$OUTPUT_TOOLCHAIN_FILE" << EOF
# CMake Toolchain File
# Generated by config_module.sh
# Target: ${HW_CONFIG[CPU_MODEL_NAME]:-Unknown}

SET(CMAKE_SYSTEM_NAME Linux)
SET(CMAKE_SYSTEM_PROCESSOR ${HW_CONFIG[ARCHITECTURE_FULL]})

# Compiler Configuration
EOF

    # Cross-Compile Pfade (falls definiert): the tools find_compilers found in this image
    if [[ -n "${COMPILER_CONFIG[CROSS_PREFIX]:-}" ]]; then
        cat >> "$OUTPUT_TOOLCHAIN_FILE" << EOF
SET(CMAKE_C_COMPILER   ${COMPILER_CONFIG[CC]})
SET(CMAKE_CXX_COMPILER ${COMPILER_CONFIG[CXX]})
SET(CMAKE_AR           ${COMPILER_CONFIG[AR]})
SET(CMAKE_STRIP        ${COMPILER_CONFIG[STRIP]})
EOF
    else
        cat >> "$OUTPUT_TOOLCHAIN_FILE" << EOF
SET(CMAKE_C_COMPILER   ${COMPILER_CONFIG[CC]})
SET(CMAKE_CXX_COMPILER ${COMPILER_CONFIG[CXX]})
EOF
    fi

    # CCACHE Integration (NEU: Wichtig für Performance!)
    if [[ -n "$ccache_path" ]]; then
        cat >> "$OUTPUT_TOOLCHAIN_FILE" << EOF

# CCACHE Configuration
SET(CMAKE_C_COMPILER_LAUNCHER "$ccache_path")
SET(CMAKE_CXX_COMPILER_LAUNCHER "$ccache_path")
EOF
        log_info "CCACHE aktiviert: $ccache_path"
    else
        log_warn "CCACHE nicht gefunden, Build wird langsamer sein."
    fi

    # Compiler Flags (the CMake options are passed on the cmake command line: COMPILER_CONFIG[CMAKE_FLAGS])
    cat >> "$OUTPUT_TOOLCHAIN_FILE" << EOF

# Compiler Flags
SET(CMAKE_C_FLAGS   "\${CMAKE_C_FLAGS} ${COMPILER_CONFIG[CFLAGS]}")
SET(CMAKE_CXX_FLAGS "\${CMAKE_CXX_FLAGS} ${COMPILER_CONFIG[CXXFLAGS]}")

# Build configuration
SET(CMAKE_BUILD_TYPE Release)
SET(BUILD_SHARED_LIBS OFF)
EOF

    log_success "CMake Toolchain generiert"
}

# ============================================================================
# BUILD CONFIG SCRIPT GENERATION (the build plan)
# ============================================================================

plan_line() { printf '%s=%q\n' "$1" "$2"; }

generate_build_config() {
    log_info "Generiere Build Config Script: $BUILD_PLAN"
    {
        echo "# Build plan, written by config_module.sh; convert_module.sh and target_module.sh read it."
        plan_line FORMAT "$FORMAT"
        plan_line BOARD "$BOARD"
        plan_line QUANT "$QUANT"
        plan_line JOB "$JOB"
        plan_line MODEL_SOURCE "$MODEL_SOURCE"
        plan_line INPUT_FORMAT "$INPUT_FORMAT"
        plan_line MODEL_NAME "$MODEL_NAME"
        plan_line OUTPUT_DIR "$OUTPUT_DIR"
        plan_line IMATRIX_DIR "$IMATRIX_DIR"
        plan_line SCRATCH_DIR "$SCRATCH_DIR"
        plan_line LLAMA_BASE "$LLAMA_BASE"
        plan_line CONVERT_SCRIPT "$CONVERT_SCRIPT"
        plan_line QUANTIZE_BIN "$QUANTIZE_BIN"
        plan_line IMATRIX_BIN "$IMATRIX_BIN"
        if [ -z "${HW_CONFIG[ARCHITECTURE_FULL]:-}" ]; then
            plan_line BOARD_PACKAGE 0
        else
            board_plan_lines
        fi
    } > "$BUILD_PLAN.tmp"
    check_plan_file "$BUILD_PLAN.tmp" "${PLAN_NAMES[@]}"
    if [ -n "${HW_CONFIG[ARCHITECTURE_FULL]:-}" ]; then
        check_plan_file "$BUILD_PLAN.tmp" "${BOARD_PLAN_NAMES[@]}"
    fi
    mv -f "$BUILD_PLAN.tmp" "$BUILD_PLAN"
    stage_output "$BUILD_PLAN"
    if [ -f "$OUTPUT_TOOLCHAIN_FILE" ]; then
        stage_output "$OUTPUT_TOOLCHAIN_FILE"
    fi
    log_success "Build Config Script generiert (checked: a valid shell script with every name the later stages read)"
}

# The board's part of the plan (in build_config.sh, or in the package plan of the target stage), from plan_board.
board_plan_lines() {
    echo "# Hardware profile of the board (for the package of llama-cli and llama-server, target stage)"
    plan_line BOARD_PACKAGE 1
    # profile: the board's hardware profile; defaults: none was given, the plain defaults of this target
    plan_line PROFILE_SOURCE "$PROFILE_SOURCE"
    if [ "$PROFILE_SOURCE" = profile ]; then
        plan_line HARDWARE_PROFILE "$HARDWARE_CONFIG_FILE"
    else
        plan_line HARDWARE_PROFILE ""
    fi
    plan_line TARGET_CPU "${HW_CONFIG[CPU_MODEL_NAME]:-}"
    plan_line PROFILE_ARCH "${HW_CONFIG[ARCHITECTURE_FULL]}"
    plan_line CFLAGS "${COMPILER_CONFIG[CFLAGS]}"
    plan_line CXXFLAGS "${COMPILER_CONFIG[CXXFLAGS]}"
    plan_line CMAKE_FLAGS "${COMPILER_CONFIG[CMAKE_FLAGS]}"
    plan_line CMAKE_TOOLCHAIN_FILE "$OUTPUT_TOOLCHAIN_FILE"
    plan_line BUILD_JOBS "${COMPILER_CONFIG[BUILD_JOBS]}"
    plan_line HW_SUPPORTS_NEON "${HW_CONFIG[SUPPORTS_NEON]:-OFF}"
    plan_line HW_SUPPORTS_AVX2 "${HW_CONFIG[SUPPORTS_AVX2]:-OFF}"
    plan_line HW_SUPPORTS_AVX512 "${HW_CONFIG[SUPPORTS_AVX512]:-OFF}"
    plan_line HW_GPU_MODEL "${HW_CONFIG[GPU_MODEL]:-None}"
}

# The board: the toolchain and the flags from the hardware profile (profile) or from the plain defaults of this
# target (defaults: a package without a profile), checked before anything uses them.
plan_board() {  # profile | defaults
    if [ "$1" = profile ]; then
        load_hardware_config
    else
        load_plain_defaults
    fi
    generate_compiler_flags
    find_compilers
    generate_cmake_toolchain
    check_toolchain_file "$OUTPUT_TOOLCHAIN_FILE"
    compiler_test
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================
main() {
    plan_request

    HARDWARE_CONFIG_FILE="${HARDWARE_CONFIG_FILE:-$BUILD_CACHE_DIR/target_hardware_config.txt}"
    rm -f "$OUTPUT_TOOLCHAIN_FILE"
    if [ -f "$HARDWARE_CONFIG_FILE" ]; then
        plan_board profile
    fi
    generate_build_config
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to config "$@"
    main "$@"
fi
