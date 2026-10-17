---
title: Converting an ONNX model to RKNN
lang: en
---
# Converting an ONNX model to RKNN

A conversion script creates an `RKNN` object, configures preprocessing and the target platform, loads the
model, builds it and exports the result:

```python
from rknn.api import RKNN

rknn = RKNN(verbose=False)
rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]], target_platform="rk3588")
rknn.load_onnx(model="model.onnx")
rknn.build(do_quantization=True, dataset="dataset.txt")
rknn.export_rknn("model.rknn")
rknn.release()
```

`mean_values` and `std_values` move the normalisation into the NPU, so the application can feed raw RGB bytes.
`target_platform` names the SoC the model will run on; a model built for the RK3588 does not load on an RK3566.

With `do_quantization=True` the weights and activations are quantized to INT8, which needs a dataset file that
lists calibration inputs, one path per line. Without quantization the model runs in FP16: slower and larger, but
no calibration is needed.

Models with dynamic input shapes need fixed sizes: pass `inputs` and `input_size_list` to `load_onnx`, or export
the ONNX file with static shapes. After the build, `rknn.accuracy_analysis(inputs=[...])` compares the output of
every layer with the float model and shows where quantization loses accuracy.
