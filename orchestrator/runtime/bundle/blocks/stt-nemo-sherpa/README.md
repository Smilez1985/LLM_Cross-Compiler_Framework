# Speech recognition with NVIDIA NeMo models (sherpa-onnx) - opt-in template

Block `stt-nemo-sherpa` (kind stt), a documented opt-in template: the generator lists it, but does not build it yet,
and it is never part of a preset.

## Models

- The program: sherpa-onnx's offline recognition server, built for the board's architecture (ELF header checked).
- An NVIDIA NeMo speech model exported to ONNX in the layout sherpa-onnx reads (`model.onnx` with its `tokens.txt`).
- Memory: about 1.6 times the model's size plus 160 MB.
- Licence: each NeMo model has its own; some are not free licences. The block's terms are shown in full before the
  block is used, and nothing of it is built, sent or fetched before you accepted them.

## What is missing

sherpa-onnx is not built by the framework yet, and a model folder needs one checked archive per component.
