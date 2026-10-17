#!/bin/bash
# convert_module.sh - Universal Model Format Converter (Template)
# Part of LLM Cross-Compiler Framework
# 
# DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.
# ZWECK: Konvertiert verschiedene Model-Formate (HF, ONNX, PyTorch) zu GGUF FP16
#        Universell einsetzbar für alle unterstützten Architekturen

set -euo pipefail

# ============================================================================
# CONFIGURATION & GLOBALS
# ============================================================================

readonly SCRIPT_NAME="convert_module.sh"
readonly SCRIPT_VERSION="1.1.0"

# Environment variables with defaults
readonly BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/build-cache}"
readonly LLAMA_CPP_PATH="${LLAMA_CPP_PATH:-${BUILD_CACHE_DIR}/repos/llama.cpp}"
readonly OUTPUT_DIR="${OUTPUT_DIR:-${BUILD_CACHE_DIR}/output}"
readonly TEMP_DIR="${TEMP_DIR:-${BUILD_CACHE_DIR}/temp}"
readonly LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Model configuration
declare -A MODEL_CONFIG
declare -A CONVERSION_STATS

# ============================================================================
# LOGGING & ERROR HANDLING
# ============================================================================

log() {
    local level="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$SCRIPT_NAME] [$level] $message" >&2
}

log_info() { [[ "$LOG_LEVEL" != "ERROR" ]] && log "INFO" "$1"; }
log_warn() { log "WARN" "$1"; }
log_error() { log "ERROR" "$1"; }
log_success() { log "SUCCESS" "$1"; }

die() {
    log_error "$1"
    exit "${2:-1}"
}

cleanup_on_error() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Conversion failed with exit code: $exit_code"
        if [[ -n "${TEMP_CONVERSION_DIR:-}" && -d "${TEMP_CONVERSION_DIR:-}" ]]; then
            rm -rf "${TEMP_CONVERSION_DIR}" 2>/dev/null || true
        fi
    fi
    exit $exit_code
}

trap cleanup_on_error ERR

# ============================================================================
# VALIDATION LOGIC
# ============================================================================

validate_huggingface_model() {
    local model_path="$1"
    
    # 1. Config Check
    if [[ ! -f "$model_path/config.json" ]]; then
        die "Missing required file: config.json"
    fi

    # 2. Tokenizer Check (Security Fix from v1.7.1)
    if [[ ! -f "$model_path/tokenizer.json" ]] && [[ ! -f "$model_path/tokenizer.model" ]]; then
         if [[ ! -f "$model_path/vocab.json" ]]; then
             die "Missing Tokenizer files! (Need tokenizer.json, tokenizer.model, or vocab.json)"
         fi
    fi
    
    log_info "Tokenizer files present."

    # 3. Weight Check
    local weight_found=0
    if [[ -f "$model_path/model.safetensors" ]] || [[ -f "$model_path/pytorch_model.bin" ]]; then
        weight_found=1
    else
        if find "$model_path" -name "model-*.safetensors" -o -name "pytorch_model-*.bin" | grep -q .; then
            weight_found=1
        fi
    fi
    
    if [[ $weight_found -eq 0 ]]; then
        die "No model weights found (safetensors/bin) in $model_path"
    fi
    
    local total_size=$(du -sb "$model_path" 2>/dev/null | cut -f1 || echo "0")
    MODEL_CONFIG[INPUT_SIZE_BYTES]="$total_size"
    MODEL_CONFIG[INPUT_SIZE_MB]="$((total_size / 1024 / 1024))"
    
    log_success "HF Validation Passed (${MODEL_CONFIG[INPUT_SIZE_MB]} MB)"
}

setup_conversion_environment() {
    mkdir -p "$OUTPUT_DIR" "$TEMP_DIR"
    if [[ ! -d "$LLAMA_CPP_PATH" ]]; then die "llama.cpp not found at: $LLAMA_CPP_PATH"; fi
}

convert_hf_to_gguf() {
    local input_path="$1"
    local output_path="$2"
    
    log_info "Converting Hugging Face model to GGUF FP16..."
    
    # Robust script finding
    local convert_script="$LLAMA_CPP_PATH/convert_hf_to_gguf.py"
    if [[ ! -f "$convert_script" ]]; then
         convert_script="$LLAMA_CPP_PATH/convert.py"
    fi
    
    if [[ ! -f "$convert_script" ]]; then
        die "Conversion script not found in llama.cpp repo"
    fi
    
    if ! python3 "$convert_script" "$input_path" --outfile "$output_path" --outtype f16; then
        die "Conversion failed. Check logs for tokenizer/weight errors."
    fi
    
    log_success "Conversion completed."
}

main() {
    local input_path=""
    local output_path=""
    local model_name=""
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --input) input_path="$2"; shift 2;;
            --output) output_path="$2"; shift 2;;
            --model-name) model_name="$2"; shift 2;;
            *) die "Unknown arg: $1";;
        esac
    done
    
    if [[ -z "$input_path" ]]; then die "--input required"; fi
    if [[ -z "$output_path" ]]; then output_path="$OUTPUT_DIR/model.fp16.gguf"; fi
    
    setup_conversion_environment
    
    if [[ -d "$input_path" ]]; then
        validate_huggingface_model "$input_path"
        convert_hf_to_gguf "$input_path" "$output_path"
    else
        die "Single file input not yet supported in this version."
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
