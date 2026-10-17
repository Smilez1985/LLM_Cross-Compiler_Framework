---
title: Installing RKNN-Toolkit2 in the build container
lang: en
---
# Installing RKNN-Toolkit2

RKNN-Toolkit2 is the Python package that converts ONNX, TensorFlow Lite, PyTorch and Caffe models into the
`.rknn` format for the NPUs of the RK3588, RK3576, RK3568 and RK3566. The conversion runs on the build host, not
on the board.

## Requirements

- A Linux host on x86_64 (recent releases also publish aarch64 wheels).
- A Python version for which a wheel exists. Wheel names carry the interpreter tag, for example `cp310` or
  `cp311`; pip refuses a wheel for another Python version with "is not a supported wheel on this platform".
- About 2 GB of free disk space for the toolkit and its pinned dependencies (onnx, onnxruntime, torch).

## Installation

Install the pinned dependencies first and the toolkit wheel second, inside the build container or a virtual
environment:

```
pip install -r requirements_cp311-2.3.0.txt
pip install rknn_toolkit2-2.3.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
```

The package is called `rknn-toolkit2`, the wheel file starts with `rknn_toolkit2`, and the Python module is
`rknn`. Check the installation with `python -c "from rknn.api import RKNN"`.

## Matching the board runtime

Models built with toolkit 2.3.0 need the NPU runtime `librknnrt.so` of the same version on the board. For Python
inference on the board install `rknn-toolkit-lite2`; it loads and runs models but cannot convert them.
