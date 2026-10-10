#!/bin/bash
# target_module.sh - Template for Quantization & Packaging
# Part of LLM Cross-Compiler Framework
# DIREKTIVE: Goldstandard. Enthält Model-Card Generierung.

set -euo pipefail

readonly BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
readonly LLAMA_CPP_PATH="${LLAMA_CPP_PATH:-${BUILD_CACHE_DIR}/repos/llama.cpp}"
readonly OUTPUT_DIR="${OUTPUT_DIR:-${BUILD_CACHE_DIR}/output}"
readonly FINAL_PACKAGE_DIR="${OUTPUT_DIR}/packages"

if [[ -f "${BUILD_CACHE_DIR}/build_config.sh" ]]; then
    source "${BUILD_CACHE_DIR}/build_config.sh"
fi

declare -A TARGET_CONFIG

log_info() { echo "ℹ️  [TARGET] $1"; }
log_success() { echo "✅ [TARGET] $1"; }
die() { echo "❌ [TARGET] $1" >&2; exit 1; }

build_native_tools() {
    log_info "Baue NATIVE Tools (x86)..."
    local build_dir="$LLAMA_CPP_PATH/build_native"
    rm -rf "$build_dir" && mkdir -p "$build_dir" && cd "$build_dir"
    cmake "$LLAMA_CPP_PATH" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_SERVER=OFF -DGGML_NATIVE=ON
    make -j$(nproc) llama-quantize
    LLAMA_CPP_QUANTIZE="$build_dir/bin/llama-quantize"
}

cross_compile_target() {
    log_info "Cross-Kompilierung für Target..."
    local build_dir="$LLAMA_CPP_PATH/build_target"
    rm -rf "$build_dir" && mkdir -p "$build_dir" && cd "$build_dir"
    cmake "$LLAMA_CPP_PATH" -DCMAKE_TOOLCHAIN_FILE="${CMAKE_TOOLCHAIN_FILE}" -DCMAKE_BUILD_TYPE=Release
    make -j$(nproc) llama-cli llama-server
    TARGET_CONFIG[LLAMA_CLI]="$build_dir/bin/llama-cli"
    TARGET_CONFIG[LLAMA_SERVER]="$build_dir/bin/llama-server"
}

quantize_model() {
    local input="${TARGET_CONFIG[INPUT_GGUF]}"
    local method="${TARGET_CONFIG[QUANT]}"
    local output="$OUTPUT_DIR/${TARGET_CONFIG[NAME]}.${method,,}.gguf"
    log_info "Quantisiere zu $method..."
    "$LLAMA_CPP_QUANTIZE" "$input" "$output" "$method"
    TARGET_CONFIG[OUTPUT_GGUF]="$output"
}

create_documentation() {
    local pkg_dir="$1"
    cat > "$pkg_dir/README.md" << EOF
---
library_name: gguf
base_model: ${TARGET_CONFIG[NAME]}
tags: [${TARGET_ARCH:-unknown}, quantization:${TARGET_CONFIG[QUANT]}, gguf]
---
# ${TARGET_CONFIG[NAME]} (${TARGET_CONFIG[QUANT]})

## 📦 Model Details
- **Format:** GGUF
- **Quantization:** ${TARGET_CONFIG[QUANT]}
- **Target:** ${TARGET_ARCH:-Generic}
- **Created:** $(date)

## 🚀 Usage
\`\`\`bash
./llama-cli -m ${TARGET_CONFIG[NAME]}.${TARGET_CONFIG[QUANT],,}.gguf -p "Hello"
\`\`\`
EOF
}

create_package() {
    local pkg_name="${TARGET_CONFIG[NAME]}_${TARGET_CONFIG[QUANT]}_${TARGET_ARCH:-gen}"
    local pkg_dir="$FINAL_PACKAGE_DIR/$pkg_name"
    mkdir -p "$pkg_dir"
    cp "${TARGET_CONFIG[OUTPUT_GGUF]}" "$pkg_dir/"
    cp "${TARGET_CONFIG[LLAMA_CLI]}" "$pkg_dir/"
    cp "${TARGET_CONFIG[LLAMA_SERVER]}" "$pkg_dir/"
    create_documentation "$pkg_dir"
    log_success "Paket erstellt: $pkg_dir"
}

main() {
    local input="" method="Q4_K_M" name=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --input) input="$2"; shift 2;;
            --quantization) method="$2"; shift 2;;
            --model-name) name="$2"; shift 2;;
            *) shift;;
        esac
    done
    TARGET_CONFIG[INPUT_GGUF]="$input"
    TARGET_CONFIG[QUANT]="$method"
    TARGET_CONFIG[NAME]="$name"
    
    build_native_tools
    quantize_model
    cross_compile_target
    create_package
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
