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
#
# Inputs (environment, set by BuildEngine):
#   MODEL_SOURCE   local HF model directory inside the container (staged by the host); an ONNX file or folder (rknn)
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
# Types accepted by llama-quantize (llama.cpp v0.6.0).
GGUF_QUANT_TYPES=" Q4_0 Q4_1 Q5_0 Q5_1 Q8_0 Q2_K Q2_K_S Q3_K_S Q3_K_M Q3_K_L Q4_K_S Q4_K_M Q5_K_S Q5_K_M Q6_K \
IQ1_S IQ1_M IQ2_XXS IQ2_XS IQ2_S IQ2_M IQ3_XXS IQ3_XS IQ3_S IQ3_M IQ4_NL IQ4_XS TQ1_0 TQ2_0 MXFP4_MOE "
# Very low-bit i-quants are unusable without an importance matrix; other i-quants lose quality without one.
GGUF_NEEDS_IMATRIX=" IQ1_S IQ1_M IQ2_XXS IQ2_XS IQ2_S "
GGUF_WANTS_IMATRIX=" IQ2_M IQ3_XXS IQ3_XS IQ3_S IQ3_M IQ4_NL IQ4_XS "
}

normalize_gguf_type() {
    local q
    q="$(echo "$1" | tr '[:lower:]' '[:upper:]')"
    case "$q" in
        ""|F16|FP16) echo "F16" ;;
        BF16) echo "BF16" ;;
        F32|FP32) echo "F32" ;;
        INT8|Q8) echo "Q8_0" ;;
        INT4|Q4) echo "Q4_K_M" ;;
        Q5) echo "Q5_K_M" ;;
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
    MODEL_NAME="${MODEL_NAME:-$(basename "${MODEL_SOURCE%/}")}"

    echo "Model:        $MODEL_SOURCE"
    echo "Format:       $FORMAT"
    echo "Board:        $BOARD"
    echo "Quantization: ${QUANT:-<default>}"
    echo "Job:          $JOB"

    [ -n "$MODEL_SOURCE" ] && [ -e "$MODEL_SOURCE" ] || die "MODEL_SOURCE '$MODEL_SOURCE' does not exist in the container."

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

    HW_CONFIG[ARCHITECTURE_FULL]="${HW_CONFIG[ARCHITECTURE_FULL]:-aarch64}"
    log_info "Hardware-Konfiguration geladen (${HW_CONFIG[CPU_MODEL_NAME]:-Unknown})"
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
        else
            log_info "Verwende generisches ARMv8-A"
            cflags="$cflags -march=armv8-a"
        fi

        if [[ "${HW_CONFIG[SUPPORTS_NEON]:-OFF}" == "ON" ]]; then
            cmake_flags="$cmake_flags -DGGML_NEON=ON"
        fi
    fi

    # x86_64 (Intel)
    if [[ "${HW_CONFIG[ARCHITECTURE_FULL]}" == "x86_64" ]]; then
        cflags="$cflags -march=native"
        if [[ "${HW_CONFIG[SUPPORTS_AVX2]:-OFF}" == "ON" ]]; then
            cmake_flags="$cmake_flags -DGGML_AVX2=ON"
        fi
    fi

    # Build-Jobs basierend auf Kernen
    if [[ -n "${HW_CONFIG[CPU_CORES]:-}" ]] && [[ "${HW_CONFIG[CPU_CORES]}" -lt "$build_jobs" ]]; then
        build_jobs=${HW_CONFIG[CPU_CORES]}
    fi

    # Speichere Konfiguration
    COMPILER_CONFIG[CFLAGS]="$cflags"
    COMPILER_CONFIG[CXXFLAGS]="$cxxflags $cflags"
    COMPILER_CONFIG[CMAKE_FLAGS]="$cmake_flags"
    COMPILER_CONFIG[BUILD_JOBS]="$build_jobs"
}

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

    # Cross-Compile Pfade (falls definiert)
    if [[ -n "${COMPILER_CONFIG[CROSS_PREFIX]:-}" ]]; then
        cat >> "$OUTPUT_TOOLCHAIN_FILE" << EOF
SET(CMAKE_C_COMPILER   /usr/bin/${COMPILER_CONFIG[CROSS_PREFIX]}-gcc)
SET(CMAKE_CXX_COMPILER /usr/bin/${COMPILER_CONFIG[CROSS_PREFIX]}-g++)
SET(CMAKE_AR           /usr/bin/${COMPILER_CONFIG[CROSS_PREFIX]}-ar)
SET(CMAKE_STRIP        /usr/bin/${COMPILER_CONFIG[CROSS_PREFIX]}-strip)
EOF
    else
        cat >> "$OUTPUT_TOOLCHAIN_FILE" << EOF
SET(CMAKE_C_COMPILER   /usr/bin/gcc)
SET(CMAKE_CXX_COMPILER /usr/bin/g++)
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
            echo "# Hardware profile of the board (for the package of llama-cli and llama-server, target stage)"
            plan_line BOARD_PACKAGE 1
            plan_line HARDWARE_PROFILE "$HARDWARE_CONFIG_FILE"
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
        fi
    } > "$BUILD_PLAN.tmp"
    mv -f "$BUILD_PLAN.tmp" "$BUILD_PLAN"
    stage_output "$BUILD_PLAN"
    if [ -f "$OUTPUT_TOOLCHAIN_FILE" ]; then
        stage_output "$OUTPUT_TOOLCHAIN_FILE"
    fi
    log_success "Build Config Script generiert"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================
main() {
    plan_request

    HARDWARE_CONFIG_FILE="${HARDWARE_CONFIG_FILE:-$BUILD_CACHE_DIR/target_hardware_config.txt}"
    rm -f "$OUTPUT_TOOLCHAIN_FILE"
    if [ -f "$HARDWARE_CONFIG_FILE" ]; then
        load_hardware_config
        generate_compiler_flags
        generate_cmake_toolchain
    fi
    generate_build_config
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to config "$@"
    main "$@"
fi
