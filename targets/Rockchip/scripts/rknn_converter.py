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

    rknn = RKNN(verbose=False)

    logger.info(f"Configuring for {target_platform.upper()}...")
    q_dtype = 'asymmetric_quantized-8' if dtype == 'i8' else 'fp16'
    rknn.config(target_platform=target_platform, optimization_level=3, quantized_dtype=q_dtype)

    logger.info(f"Loading model: {os.path.basename(onnx_path)}")
    if rknn.load_onnx(model=onnx_path) != 0:
        logger.error("Load failed!")
        sys.exit(1)

    logger.info(f"Building (Quant: {dtype}, Dataset: {dataset_path or 'None'})...")
    do_quant = (dtype == 'i8')
    
    # DATASET LOGIC
    # If do_quant is True but no dataset, RKNN might fail or do hybrid.
    # We pass the path if provided.
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
