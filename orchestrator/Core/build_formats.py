"""
Model formats and optimization levels of a build.

They live in this small module so the CLI can build its --format choices without importing the build engine and
the Docker SDK (lazy loading). orchestrator.Core.builder re-exports both: builder.ModelFormat is this class.
"""

from enum import Enum


class ModelFormat(Enum):
    HUGGINGFACE = "hf"
    GGUF = "gguf"
    RKLLM = "rkllm"          # Rockchip NPU LLM (RK3588/RK3576)
    ONNX = "onnx"
    TENSORFLOW_LITE = "tflite"
    PYTORCH_MOBILE = "pytorch_mobile"
    # Hardware-Specific Formats
    RKNN = "rknn"           # Rockchip NPU
    TENSORRT = "tensorrt"   # NVIDIA GPU
    OPENVINO = "openvino"   # Intel NPU/CPU
    COREML = "coreml"       # Apple Silicon
    NCNN = "ncnn"           # Mobile High-Performance
    MNN = "mnn"             # Alibaba Mobile


class OptimizationLevel(Enum):
    FAST = "fast"
    BALANCED = "balanced"
    SIZE = "size"
    SPEED = "speed"
    AGGRESSIVE = "aggressive"
