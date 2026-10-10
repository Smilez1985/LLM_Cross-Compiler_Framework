#!/usr/bin/env python3
"""
RKNN Converter Script
Bridge between Shell and RKNN Toolkit2 API.
"""

import sys
import os
import argparse
import logging
import time

logging.basicConfig(level=logging.INFO, format='[RKNN-PY] %(message)s')
logger = logging.getLogger("RKNN_Converter")

def convert_to_rknn(onnx_path, output_path, target_platform, dtype='i8', dataset_path=None):
    try:
        from rknn.api import RKNN
    except ImportError:
        logger.error("CRITICAL: 'rknn' module not found.")
        sys.exit(1)

    output_path = os.path.abspath(output_path)  # the build may change the working directory
    rknn = RKNN(verbose=False)

    logger.info(f"Configuring for {target_platform.upper()}...")
    # rknn-toolkit2 >= 2.x names quantization schemes w8a8/w8a16/...; FP16 simply means no quantization.
    config_kwargs = {"target_platform": target_platform, "optimization_level": 3}
    if dtype == 'i8':
        config_kwargs["quantized_dtype"] = "w8a8"
    rknn.config(**config_kwargs)

    logger.info(f"Loading model: {os.path.basename(onnx_path)}")
    if rknn.load_onnx(model=onnx_path) != 0:
        logger.error("Load failed!")
        sys.exit(1)

    logger.info(f"Building (Quant: {dtype}, Dataset: {dataset_path or 'None'})...")
    do_quant = (dtype == 'i8')
    
    # The dataset lists sample inputs; relative entries are relative to the list file, so build from there.
    if dataset_path:
        dataset_path = os.path.abspath(dataset_path)
        os.chdir(os.path.dirname(dataset_path))
    if rknn.build(do_quantization=do_quant, dataset=dataset_path) != 0:
        logger.error("Build failed!")
        sys.exit(1)

    logger.info(f"Exporting to {output_path}...")
    if rknn.export_rknn(output_path) != 0:
        logger.error("Export failed!")
        sys.exit(1)

    logger.info("✅ Success.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--target', default='rk3566')
    parser.add_argument('--dtype', default='i8')
    parser.add_argument('--dataset', default=None, help='Path to calibration dataset')
    args = parser.parse_args()
    
    convert_to_rknn(args.model, args.output, args.target, args.dtype, args.dataset)
