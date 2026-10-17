#!/usr/bin/env python3
"""Convert a HuggingFace model directory to .rkllm with RKLLM-Toolkit (run inside /opt/venv-rkllm)."""

import argparse
import json
import sys
import tempfile

# Native dtypes of RKLLM-Toolkit 1.3.x; the toolkit itself rejects combinations a SoC does not support.
QUANT_TYPES = ["w4a16", "w4a16_g32", "w4a16_g64", "w4a16_g128",
               "w8a8", "w8a8_g128", "w8a8_g256", "w8a8_g512", "w4a8_g32"]
NPU_CORES = {"rk3588": 3, "rk3576": 2}


def parse_args():
    parser = argparse.ArgumentParser(description="Export a HuggingFace model to RKLLM")
    parser.add_argument("--model", required=True, help="HuggingFace model directory")
    parser.add_argument("--output", required=True, help="Output .rkllm file")
    parser.add_argument("--quant", default="w8a8", choices=QUANT_TYPES)
    parser.add_argument("--target", default="rk3588", choices=sorted(NPU_CORES))
    parser.add_argument("--algorithm", default="normal", choices=["normal", "grq"],
                        help="Quantization algorithm (grq: higher accuracy, slower conversion)")
    parser.add_argument("--optimization-level", type=int, default=1, choices=[0, 1])
    parser.add_argument("--max-context", type=int, default=None, help="Maximum context length")
    parser.add_argument("--dataset", default=None,
                        help="Calibration text (one sample per paragraph) or RKLLM JSON dataset")
    return parser.parse_args()


def calibration_json(path: str) -> str:
    """RKLLM expects JSON [{"input": ..., "target": ...}]; plain text files are converted."""
    if path.endswith(".json"):
        return path
    with open(path, encoding="utf-8", errors="ignore") as f:
        samples = [p.strip() for p in f.read().split("\n\n") if p.strip()][:64]
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump([{"input": s, "target": ""} for s in samples], out)
    out.close()
    return out.name


def main():
    args = parse_args()
    try:
        from rkllm.api import RKLLM
    except ImportError:
        print("ERROR: rkllm toolkit is not installed in this Python environment.", file=sys.stderr)
        return 1

    print(f"--- RKLLM export: {args.model} -> {args.output} ({args.quant}, {args.target}) ---")
    llm = RKLLM()

    ret = llm.load_huggingface(model=args.model)
    if ret != 0:
        print(f"ERROR: load_huggingface failed with code {ret}", file=sys.stderr)
        return ret

    build_kwargs = dict(
        do_quantization=True,
        optimization_level=args.optimization_level,
        quantized_dtype=args.quant,
        quantized_algorithm=args.algorithm,
        target_platform=args.target,
        num_npu_core=NPU_CORES[args.target],
    )
    if args.dataset:
        build_kwargs["dataset"] = calibration_json(args.dataset)
    if args.max_context:
        build_kwargs["max_context"] = args.max_context

    ret = llm.build(**build_kwargs)
    if ret != 0:
        print(f"ERROR: build failed with code {ret}", file=sys.stderr)
        return ret

    ret = llm.export_rkllm(args.output)
    if ret != 0:
        print(f"ERROR: export failed with code {ret}", file=sys.stderr)
        return ret

    print("Conversion completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
