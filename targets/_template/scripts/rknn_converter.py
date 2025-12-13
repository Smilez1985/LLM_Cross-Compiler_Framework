#!/usr/bin/env python3
"""
RKNN Converter Script for LLM Framework
DIREKTIVE: Goldstandard, Error-Handling, Logging.
Zweck: Bridge zwischen Shell-Modul und RKNN Toolkit2 API.
"""

import sys
import os
import argparse
import logging
import time

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [RKNN-PY] - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("RKNN_Converter")

def check_rknn_installation():
    """Verify RKNN Toolkit2 installation"""
    try:
        from rknn.api import RKNN
        return RKNN
    except ImportError:
        logger.error("CRITICAL: 'rknn' module not found.")
        logger.error("Please ensure rknn-toolkit2 is installed in the Docker container.")
        sys.exit(1)

def convert_to_rknn(onnx_path, output_path, target_platform, dtype='i8'):
    """Execute the full conversion pipeline"""
    RKNN = check_rknn_installation()
    rknn = RKNN(verbose=False) # Set True for more debug output

    # 1. Configuration
    logger.info(f"Configuring for target: {target_platform.upper()}")
    
    # Map 'i8' to toolkit specific strings
    # i8 = asymmetric_quantized-8 (Standard für NPU Effizienz)
    # fp16 = floating-point 16 (Höhere Genauigkeit, weniger NPU-Speed-Up)
    quantized_dtype = 'asymmetric_quantized-8' if dtype == 'i8' else 'fp16'
    
    try:
        rknn.config(
            target_platform=target_platform,
            optimization_level=3, # Max optimization
            quantized_dtype=quantized_dtype
        )
    except Exception as e:
        logger.error(f"Configuration failed: {e}")
        sys.exit(2)

    # 2. Load Model
    logger.info(f"Loading ONNX model: {os.path.basename(onnx_path)}")
    ret = rknn.load_onnx(model=onnx_path)
    if ret != 0:
        logger.error("Load ONNX failed! Check if the model file is valid.")
        sys.exit(3)

    # 3. Build (Quantization & Optimization)
    logger.info(f"Building RKNN model (Type: {dtype})...")
    logger.info("This can take several minutes...")
    
    # Entscheidung: Quantisierung oder nicht?
    # Für i8 brauchen wir normalerweise einen Kalibrierungsdatensatz.
    # Wenn keiner da ist, macht das Toolkit oft einen Fallback oder Hybrid-Modus.
    # Wir setzen do_quantization auf True nur für i8.
    do_quant = (dtype == 'i8')
    
    # dataset=None bedeutet: Nutze Standard-Parameter (kann bei komplexen Modellen Genauigkeit kosten,
    # ist aber für den automatisierten Pipeline-Ansatz der erste Schritt).
    ret = rknn.build(do_quantization=do_quant, dataset=None)
    
    if ret != 0:
        logger.error("Build RKNN failed during graph optimization/quantization.")
        sys.exit(4)

    # 4. Export
    logger.info(f"Exporting RKNN binary to: {output_path}")
    ret = rknn.export_rknn(output_path)
    if ret != 0:
        logger.error("Export RKNN failed!")
        sys.exit(5)

    logger.info("✅ Conversion completed successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='High-Performance ONNX to RKNN Converter')
    parser.add_argument('--model', required=True, help='Path to input ONNX model')
    parser.add_argument('--output', required=True, help='Path to output RKNN file')
    parser.add_argument('--target', default='rk3566', help='Target NPU platform (rk3566, rk3588)')
    parser.add_argument('--dtype', default='i8', choices=['i8', 'fp16'], help='Quantization type (i8 or fp16)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.model):
        logger.error(f"Input file not found: {args.model}")
        sys.exit(1)
        
    # Ensure output dir exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
        
    start_ts = time.time()
    convert_to_rknn(args.model, args.output, args.target, args.dtype)
    logger.info(f"Total time: {time.time() - start_ts:.2f}s")
