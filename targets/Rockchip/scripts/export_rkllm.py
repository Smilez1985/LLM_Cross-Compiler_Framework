#!/usr/bin/env python3
"""
RKLLM Export Script
Stand-alone Python script to convert HuggingFace models to RKLLM format.
Usage: python3 export_rkllm.py --model <path> --output <path> --quant <type> --target <platform>
"""

import sys
import argparse
import os

def parse_args():
    parser = argparse.ArgumentParser(description="Export HF Model to RKLLM")
    parser.add_argument("--model", required=True, help="Path to HuggingFace model directory")
    parser.add_argument("--output", required=True, help="Output path for .rkllm file")
    parser.add_argument("--quant", default="w8a8", choices=["w8a8", "w4a16"], help="Quantization type")
    parser.add_argument("--target", default="rk3588", help="Target NPU Platform")
    return parser.parse_args()

def main():
    args = parse_args()
    
    # Lazy Import, damit das Skript auch ohne SDK importierbar ist (für Tests)
    try:
        from rkllm.api import RKLLM
    except ImportError:
        print("CRITICAL ERROR: 'rkllm' module not found.")
        sys.exit(1)

    print(f"--- RKLLM Exporter: {args.model} -> {args.output} ({args.quant}) ---")

    llm = RKLLM()

    # 1. Load
    ret = llm.load_huggingface(model=args.model)
    if ret != 0:
        print(f"❌ Load failed (Code {ret})")
        sys.exit(ret)

    # 2. Build
    ret = llm.build(
        do_quantization=True,
        optimization_level=1,
        quantized_dtype=args.quant,
        target_platform=args.target,
        num_npu_core=3 
    )
    if ret != 0:
        print(f"❌ Build failed (Code {ret})")
        sys.exit(ret)

    # 3. Export
    ret = llm.export_rkllm(args.output)
    if ret != 0:
        print(f"❌ Export failed (Code {ret})")
        sys.exit(ret)

    print("✅ Conversion completed.")

if __name__ == "__main__":
    main()
