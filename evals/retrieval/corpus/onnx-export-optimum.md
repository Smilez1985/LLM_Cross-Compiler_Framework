---
title: Exporting a Hugging Face model to ONNX
lang: en
---
# Exporting a Hugging Face model to ONNX

NPU toolkits such as RKNN-Toolkit2 take ONNX files as input. Transformer models from the Hugging Face Hub are
exported with Optimum:

```
pip install "optimum[exporters]"
optimum-cli export onnx --model <repository> --task image-classification --opset 17 out/
```

`--task` selects the model head; without it Optimum guesses from the model configuration. The opset must be
one the downstream converter supports; check its documentation before choosing the newest.

Exported graphs have dynamic batch and sequence axes. Converters for embedded NPUs usually need fixed shapes:
export with fixed input sizes, or fix them afterwards and simplify the graph with `onnxsim model.onnx
model-sim.onnx --overwrite-input-shape input:1,3,224,224`. Check the result with `onnxruntime` on the host
before converting it further, so conversion errors are not mistaken for export errors.
