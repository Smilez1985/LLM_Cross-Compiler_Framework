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
#         With a hardware profile in the build plan (config_module.sh) a GGUF build is also packaged for the board, as
#         in version 1.x: llama-cli and llama-server cross-compiled from the image's llama.cpp source with the
#         profile's toolchain, a model card and helper scripts, in output/packages/. A build of the BuildEngine has no
#         profile: the board's runtime comes from the runtime builder (llm-cli device runtime build) and the board
#         package from llm-cli deploy package or llm-cli device runtime deploy.

set -euo pipefail

MODULES_DIR="${MODULES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# The paths of the build plan and die() come from the stage that writes the plan.
# shellcheck source=config_module.sh
source "$MODULES_DIR/config_module.sh"
QUANTIZE_JOBS="$PLAN_DIR/quantize_jobs.sh"

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
    "$QUANTIZE_BIN" "$@" "$f16_file" "$out_file" "$q"
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
    cat > "$OUTPUT_DIR/build_info.json" <<EOF
{"model": "$MODEL_NAME", "format": "$FORMAT", "board": "$BOARD", "quantization": "${QUANT:-default}", "llama_cpp": "$(cat "$LLAMA_BASE/.llama_cpp_tag" 2>/dev/null || echo unknown)"}
EOF
}

# ============================================================================
# PACKAGE FOR THE BOARD (version 1.x; only with a hardware profile)
# ============================================================================

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

cross_compile_target() {
    log_info "Schritt 1B: Cross-Kompilierung für Target (${PROFILE_ARCH:-unknown})..."

    # In the work folder: the image's llama.cpp source is read-only in a build container.
    local build_dir="$SCRATCH_DIR/build_target"
    rm -rf "$build_dir" && mkdir -p "$build_dir"
    cd "$build_dir"

    local cmake_args=()
    cmake_args+=("-DCMAKE_BUILD_TYPE=Release")
    cmake_args+=("-DBUILD_SHARED_LIBS=OFF")
    cmake_args+=("-DLLAMA_CURL=OFF")
    # Nothing is downloaded at configure time (the build container has no network); only what the package needs.
    cmake_args+=("-DFETCHCONTENT_FULLY_DISCONNECTED=ON" "-DLLAMA_OPENSSL=OFF" "-DLLAMA_BUILD_TESTS=OFF")
    cmake_args+=("-DLLAMA_BUILD_SERVER=ON")

    # WICHTIG: Setze die Toolchain (mit den Optimierungs-Flags) aus config_module.sh
    cmake_args+=("-DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}")
    local flags=()
    read -r -a flags <<< "${CMAKE_FLAGS:-}"
    if [ ${#flags[@]} -gt 0 ]; then
        cmake_args+=("${flags[@]}")
    fi

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

create_package_documentation() {
    local package_dir="$1"
    local readme_file="$package_dir/README.md"
    local model_file
    model_file="$(basename "${TARGET_CONFIG[INPUT_GGUF]}")"

    log_info "Generiere Gold Standard Model Card..."

    cat > "$readme_file" << EOF
---
library_name: gguf
base_model: ${TARGET_CONFIG[MODEL_NAME]}
tags:
- rockchip
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
- **File Size:** ${QUANTIZATION_STATS[OUTPUT_SIZE_MB]:-0} MB
- **Optimization Flags:** \`${CFLAGS:-none}\`

## 🚀 Quick Start (On ${PROFILE_ARCH:-the board})

### Option A: Direct CLI
\`\`\`bash
# Make binaries executable
chmod +x llama-cli llama-server

# Run inference
./llama-cli -m ${model_file} -p "Hello, AI!" -n 128
\`\`\`

### Option B: API Server
\`\`\`bash
# Start server on port 8080
./llama-server -m ${model_file} --host 0.0.0.0 --port 8080
\`\`\`

## ⚠️ Requirements
- **Memory:** Approx. ${QUANTIZATION_STATS[OUTPUT_SIZE_MB]:-0} MB + 500MB Overhead
- **OS:** Linux (${PROFILE_ARCH:-aarch64})

## 🛠️ Build Info
Generated by LLM Cross-Compiler Framework.
- **Date:** $(date -Iseconds)
- **Build Time:** ${BUILD_STATS[BUILD_TIME]:-0}s

EOF
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

    # Generiere Dokumentation & Helper
    create_package_documentation "$package_dir"
    create_helper_scripts "$package_dir"
    create_package_manifest "$package_dir"

    # Symlink
    local latest_link="$final_package_dir/${model_name}_${quant_method,,}_latest"
    rm -f "$latest_link"
    ln -s "$package_name" "$latest_link"

    log_success "Deployment package created at: $package_dir"
}

create_package_manifest() {
    local package_dir="$1"
    cat > "$package_dir/MANIFEST.json" << EOF
{
  "name": "${TARGET_CONFIG[PACKAGE_NAME]}",
  "created": "$(date -Iseconds)",
  "model": "${TARGET_CONFIG[MODEL_NAME]}",
  "quantization": "${TARGET_CONFIG[QUANT_METHOD]}"
}
EOF
}

create_helper_scripts() {
    local package_dir="$1"
    cat > "$package_dir/test_model.sh" << 'EOF'
#!/bin/bash
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MODEL=$(find "$DIR" -name "*.gguf" | head -n 1)
CLI="$DIR/llama-cli"
if [ ! -x "$CLI" ]; then chmod +x "$CLI"; fi
echo "Testing model: $(basename "$MODEL")"
"$CLI" -m "$MODEL" -p "Hello, world!" -n 10
EOF
    chmod +x "$package_dir/test_model.sh"

    cat > "$package_dir/deploy.sh" << 'EOF'
#!/bin/bash
set -e
DEST="${1:-/opt/llm}"
echo "Deploying to $DEST..."
mkdir -p "$DEST"
cp -r * "$DEST/"
echo "Done."
EOF
    chmod +x "$package_dir/deploy.sh"
}

print_final_summary() {
    echo ""
    echo "=================================================="
    echo "       BUILD & QUANTIZATION COMPLETED"
    echo "=================================================="
    echo " Model:         ${TARGET_CONFIG[MODEL_NAME]}"
    echo " Quantization:  ${TARGET_CONFIG[QUANT_METHOD]}"
    echo " Target Arch:   ${PROFILE_ARCH:-unknown}"
    echo " Package:       ${TARGET_CONFIG[PACKAGE_DIR]}"
    echo " Model Card:    Generated (README.md)"
    echo "=================================================="
    echo ""
}

package_for_board() {
    local f
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
    cross_compile_target
    create_deployment_package
    print_final_summary
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    # shellcheck source=/dev/null
    source "$BUILD_PLAN"

    quantize_model
    write_build_info
    if [ "$BOARD_PACKAGE" = "1" ] && [ "$FORMAT" = "gguf" ]; then
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
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to target "$@"
    main "$@"
fi
