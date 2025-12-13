#!/usr/bin/env python3
"""
RKLLM Export Script (Template)
Stand-alone Python script to convert HuggingFace models to RKLLM format.
This script is intended to be run inside the build container.
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
    
    print(f"--- RKLLM Exporter (Template) ---")
    print(f"Model:  {args.model}")
    print(f"Target: {args.target}")
    print(f"Quant:  {args.quant}")

    # Lazy import to allow script existence without immediate crash if SDK missing
    try:
        from rkllm.api import RKLLM
    except ImportError:
        print("CRITICAL: 'rkllm' module not found.")
        print("Please ensure the RKLLM-Toolkit is installed in your Docker image.")
        sys.exit(1)

    llm = RKLLM()

    # 1. Load Model
    print(f"Loading model from {args.model}...")
    # Note: load_huggingface expects the directory containing config.json
    ret = llm.load_huggingface(model=args.model)
    if ret != 0:
        print(f"❌ Load failed (Code {ret})")
        sys.exit(ret)

    # 2. Build
    print(f"Building model for {args.target}...")
    ret = llm.build(
        do_quantization=True,
        optimization_level=1,
        quantized_dtype=args.quant,
        target_platform=args.target,
        num_npu_core=3 # Defaulting to 3 cores for RK3588
    )
    if ret != 0:
        print(f"❌ Build failed (Code {ret})")
        sys.exit(ret)

    # 3. Export
    print(f"Exporting to {args.output}...")
    ret = llm.export_rkllm(args.output)
    if ret != 0:
        print(f"❌ Export failed (Code {ret})")
        sys.exit(ret)

    print("✅ Conversion completed successfully.")

if __name__ == "__main__":
    main()
