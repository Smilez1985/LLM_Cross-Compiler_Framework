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
#         With a hardware profile in the build plan (config_module.sh) the model is also packaged for the board, as in
#         version 1.x: llama-cli and llama-server cross-compiled with the profile's toolchain and a model card, in
#         output/packages/. A build of the BuildEngine has no profile: the board's runtime comes from the runtime
#         builder (llm-cli device runtime build).

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
QUANTIZED_FROM=()

log_info() { echo "ℹ️  [TARGET] $1"; }
log_success() { echo "✅ [TARGET] $1"; }

# One job of quantize_jobs.sh: TYPE F16_FILE OUTPUT_FILE [llama-quantize options, e.g. --imatrix FILE].
# quantize_kept_gguf keeps the F16 file (a GGUF input, or the file named with --output); quantize_gguf removes it
# afterwards (the intermediate of the convert stage).
quantize_kept_gguf() {
    local q="$1" f16_file="$2" out_file="$3"
    shift 3
    echo ">> Quantizing -> $q"
    "$QUANTIZE_BIN" "$@" "$f16_file" "$out_file" "$q"
    TARGET_CONFIG[OUTPUT_GGUF]="$out_file"
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

# --- Package for the board (version 1.x; only with a hardware profile) ---------------------------------------------

cross_compile_target() {
    log_info "Cross-Kompilierung für Target..."
    # In the work folder: the image's llama.cpp source is read-only in a build container.
    local build_dir="$SCRATCH_DIR/build_target"
    local flags=()
    read -r -a flags <<< "${CMAKE_FLAGS:-}"
    rm -rf "$build_dir" && mkdir -p "$build_dir" && cd "$build_dir"
    # Nothing is downloaded at configure time (the build container has no network); only what the package needs.
    cmake "$LLAMA_BASE" -DCMAKE_TOOLCHAIN_FILE="${CMAKE_TOOLCHAIN_FILE}" -DCMAKE_BUILD_TYPE=Release \
        -DFETCHCONTENT_FULLY_DISCONNECTED=ON -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=ON \
        "${flags[@]}"
    make -j"$(nproc)" llama-cli llama-server
    TARGET_CONFIG[LLAMA_CLI]="$build_dir/bin/llama-cli"
    TARGET_CONFIG[LLAMA_SERVER]="$build_dir/bin/llama-server"
}

create_documentation() {
    local pkg_dir="$1" model_file
    model_file="$(basename "${TARGET_CONFIG[OUTPUT_GGUF]}")"
    cat > "$pkg_dir/README.md" << EOF
---
library_name: gguf
base_model: ${TARGET_CONFIG[NAME]}
tags: [${PROFILE_ARCH:-unknown}, quantization:${TARGET_CONFIG[QUANT]}, gguf]
---
# ${TARGET_CONFIG[NAME]} (${TARGET_CONFIG[QUANT]})

## 📦 Model Details
- **Format:** GGUF
- **Quantization:** ${TARGET_CONFIG[QUANT]}
- **Target:** ${PROFILE_ARCH:-Generic}
- **Created:** $(date)

## 🚀 Usage
\`\`\`bash
./llama-cli -m ${model_file} -p "Hello"
\`\`\`
EOF
}

create_package() {
    local pkg_name="${TARGET_CONFIG[NAME]}_${TARGET_CONFIG[QUANT]}_${PROFILE_ARCH:-gen}"
    local pkg_dir="$OUTPUT_DIR/packages/$pkg_name"
    mkdir -p "$pkg_dir"
    cp "${TARGET_CONFIG[OUTPUT_GGUF]}" "$pkg_dir/"
    cp "${TARGET_CONFIG[LLAMA_CLI]}" "$pkg_dir/"
    cp "${TARGET_CONFIG[LLAMA_SERVER]}" "$pkg_dir/"
    create_documentation "$pkg_dir"
    log_success "Paket erstellt: $pkg_dir"
}

package_for_board() {
    local f
    if [ -z "${TARGET_CONFIG[OUTPUT_GGUF]:-}" ]; then
        for f in "$OUTPUT_DIR"/*.gguf "${CONVERT_OUTPUT:-}"; do
            if [ -f "$f" ]; then
                TARGET_CONFIG[OUTPUT_GGUF]="$f"
                break
            fi
        done
    fi
    [ -n "${TARGET_CONFIG[OUTPUT_GGUF]:-}" ] || die "No GGUF model in $OUTPUT_DIR to package for the board."
    TARGET_CONFIG[QUANT]="$QUANT_TYPE"
    TARGET_CONFIG[NAME]="$MODEL_NAME"
    cross_compile_target
    create_package
}

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
