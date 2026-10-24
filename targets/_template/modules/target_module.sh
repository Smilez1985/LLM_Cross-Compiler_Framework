#!/bin/bash
# target_module.sh - stage 4 of the build of [MODULE_NAME]: quantization, packaging and the results (run by build.sh,
# the stage runner)
# Part of LLM Cross-Compiler Framework
#
# Image:  this target's Dockerfile (base image debian:bookworm-slim) for the build host's architecture; llama-quantize
#         is the image's own (build/bin of the pinned llama.cpp, built for the build host).
# Target: [MODULE_NAME] ([IHRE_ARCHITEKTUR] Linux).
# Stage:  quantizes what the convert stage left for it (quantize_jobs.sh: the F16 intermediate to the requested GGUF
#         type, with the importance matrix when the build has one) and removes the intermediate (a GGUF input and a
#         file named with --output stay); runs the packaging
#         commands of the module definition (after the SDK setup of the source stage); a build that wrote no artifact
#         fails; writes build_info.json, which the BuildEngine completes with the file list, the hashes and the build
#         data; and lists the results.
#         The package for the board (version 1.x's target module: deployment-ready, with documentation, helper
#         scripts, a complete manifest and automated tests), in output/packages/<model>_<type>_<arch>_<time>/: the GGUF,
#         llama-cli and llama-server cross-compiled with the plan's toolchain, the toolchain file and the hardware
#         profile, a model card (README.md), test_model.sh and deploy.sh (from scripts/package_*.sh), MANIFEST.json
#         with every build detail and SHA256SUMS (scripts/board_package.py); checked before it is kept (a run of the
#         model with the image's llama-completion, ELF programs for [IHRE_ARCHITEKTUR], every file against SHA256SUMS).
#         It is made with a hardware profile in the build plan (config_module.sh), and on request (build.sh --package:
#         target_module.sh started by hand, the entrypoint modes target and pipeline) also without one, with the
#         module's own flags (plain defaults), which the manifest names. A build of the BuildEngine gets the board's
#         stored probe when there is one (DECISIONS round 23 item 5), and then makes the package too.

set -euo pipefail

MODULES_DIR="${MODULES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# The SDK environment (source_module.sh) and the plan paths and die() (config_module.sh) of the stages before.
# shellcheck source=source_module.sh
source "$MODULES_DIR/source_module.sh"
# shellcheck source=config_module.sh
source "$MODULES_DIR/config_module.sh"
QUANTIZE_JOBS="$PLAN_DIR/quantize_jobs.sh"
# The convert stage's statistics (input, output, ratio, duration, functional test): "conversion" in build_info.json
CONVERT_STATS="$PLAN_DIR/convert_stats.json"

declare -A TARGET_CONFIG
declare -A QUANTIZATION_STATS
declare -A BUILD_STATS
QUANTIZED_FROM=()

log_info() { echo "ℹ️  [TARGET] $1"; }
log_success() { echo "✅ [TARGET] $1"; }
log_warn() { echo "⚠️  [TARGET] $1"; }

# The name of this target in the package (model card tag, manifest)
PACKAGE_TARGET='[MODULE_NAME]'

# One job of quantize_jobs.sh: TYPE F16_FILE OUTPUT_FILE [llama-quantize options, e.g. --imatrix FILE].
# quantize_kept_gguf keeps the F16 file (a GGUF input, or the file named with --output); quantize_gguf removes it
# afterwards (the intermediate of the convert stage).
quantize_kept_gguf() {
    local q="$1" f16_file="$2" out_file="$3"
    shift 3
    echo ">> Quantizing -> $q"
    "$QUANTIZE_BIN" "$@" "$f16_file" "$out_file" "$q"
    [ -s "$out_file" ] || die "llama-quantize ended without writing the $q model."
    # OUTPUT_GGUF: the name module definitions (packaging commands) have used
    TARGET_CONFIG[OUTPUT_GGUF]="$out_file"
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
    cat > "$OUTPUT_DIR/build_info.json" <<EOF
{"model": "$MODEL_NAME", "format": "$FORMAT", "board": "${BOARD:-default}", "quantization": "$QUANT_TYPE", "llama_cpp": "$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)"$conversion}
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

# The first of the module's CMake flags (FLAG...: the plan's CMAKE_FLAGS, word by word as the cross build passes them)
# that the cross build refuses, with the reason; nothing when there is none. The options of every build come after
# the module's flags and win (cross_compile_target), except an option of NEVER_SET_OPTIONS, which downloads whenever it
# is set, and a CMake script (-C, an initial cache, or -P), which can set any option. cmake reads "-D NAME=VALUE" as
# one option, and -DNAME:TYPE=VALUE names NAME.
module_flag_problem() {  # FLAG...
    local word name
    while [ $# -gt 0 ]; do
        word="$1"
        shift
        if [ "$word" = -D ] && [ $# -gt 0 ]; then
            word="-D$1"
            shift
        fi
        case "$word" in
            -C*|-P*)
                echo "$word (CMake runs a script with -C or -P, and a script can set any option)"
                return ;;
            -D*)
                name="${word#-D}"
                name="${name%%=*}"
                name="${name%%:*}"
                if [[ " ${NEVER_SET_OPTIONS[*]} " == *" $name "* ]]; then
                    echo "$word ($name downloads at configure time and is never set)"
                    return
                fi ;;
        esac
    done
}

check_module_flags() {  # before anything slow: the module's CMake flags in the plan, as the cross build passes them
    local problem flags=()
    read -r -a flags <<< "${CMAKE_FLAGS:-}"
    [ ${#flags[@]} -gt 0 ] || return 0
    problem="$(module_flag_problem "${flags[@]}")"
    [ -z "$problem" ] || die "The CMake flags of the build plan would let llama.cpp download while it is built: $problem; builds have no network: leave it out. No package was made."
}

# A short run of the quantized model on this build machine: the convert stage's functional test (scripts/model_check.py
# test, the one implementation), with the image's own llama-completion (llama-cli when the image has only that): 8
# tokens, at most FUNCTIONAL_TEST_SECONDS, offline, the options this tool has. A run that ends with an error or
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
            log_warn "The functional test did not finish within ${FUNCTIONAL_TEST_SECONDS} s; the package is made anyway." ;;
        stopped) log_warn "The functional test was stopped from outside (${tool:-the tool}); the package is made anyway." ;;
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
                 "-DLLAMA_LLGUIDANCE=OFF" "-DGGML_CPU_KLEIDIAI=OFF" "-DGGML_OPENMP_FETCH=OFF")

    log_info "Configuring target build..."
    if ! cmake "$LLAMA_BASE" "${cmake_args[@]}"; then
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
        --int "build.jobs=${BUILD_JOBS:-4}" \
        --set "build.framework=LLM Cross-Compiler Framework" \
        --set "hardware.profile=$(profile_text)" \
        --set "hardware.source=${PROFILE_SOURCE:-defaults}" \
        --set "hardware.arch=${PROFILE_ARCH:-}" \
        --set "hardware.cpu=${TARGET_CPU:-generic}" \
        --set "hardware.cflags=${CFLAGS:-}" \
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

main() {
    # shellcheck source=/dev/null
    source "$BUILD_PLAN"
    load_sdk_environment

    quantize_model

    # --- PACKAGING ---
    # [PACKAGING_COMMANDS]

    # A branch that wrote nothing is a failed build, not an empty success (the model may be the --output file).
    [ -n "$(ls -A "$OUTPUT_DIR")" ] || [ -f "${CONVERT_OUTPUT:-}" ] \
        || die "No artifact in $OUTPUT_DIR for quantization '$QUANT_TYPE'."

    write_build_info
    if [ "$FORMAT" = "gguf" ] && { [ "$BOARD_PACKAGE" = "1" ] || [ "${PACKAGE_REQUESTED:-0}" = "1" ]; }; then
        package_for_board
    fi

    echo "=== Artifacts ==="
    ls -la "$OUTPUT_DIR"

    local f
    while IFS= read -r f; do
        stage_output "$f"
    done < <(find "$OUTPUT_DIR" -type f)
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the stage runner, with the package for the board (version
    # 1.x: target_module.sh quantized and packaged).
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to target --package "$@"
    main "$@"
fi
