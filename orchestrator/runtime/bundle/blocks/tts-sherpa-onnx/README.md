# Speech output (sherpa-onnx) - template

Block `tts-sherpa-onnx` (kind tts), a documented template: the generator lists it, but does not build it yet.

## Models

- The program: sherpa-onnx's offline TTS server, built for the board's architecture (ELF header checked).
- A TTS model in the layout sherpa-onnx reads (format `sherpa-onnx`): the ONNX model (`model.onnx`), its
  `tokens.txt`, and for VITS/Piper voices the `espeak-ng-data` folder. Kokoro, VITS and Matcha models from the
  sherpa-onnx model list fit; a plain ONNX file from elsewhere does not.
- Memory: about twice the model's size plus 128 MB.
- Licence: each voice has its own (Piper voices: mostly MIT or CC-BY; check the model card).
- The block's terms name the parts it brings in beside the voice: a prebuilt onnxruntime (MIT, with its third-party
  notices) and the eSpeak NG data (GPL-3.0-or-later). Their text is shown in full before the block is used, and
  nothing of it is built, sent or fetched before you accepted them.

## What is missing

A bundle file is one file today; the model folder (espeak-ng-data) needs one checked archive per component. Until then
this block stays a template.
