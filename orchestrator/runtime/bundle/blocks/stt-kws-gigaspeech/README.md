# Wake word with a GigaSpeech keyword model (sherpa-onnx) - opt-in template

Block `stt-kws-gigaspeech` (kind stt), a documented opt-in template: the generator lists it, but does not build it
yet, and it is never part of a preset.

## Models

- The program: sherpa-onnx's keyword spotter, built for the board's architecture (ELF header checked).
- A small keyword-spotting model trained on the GigaSpeech corpus (`encoder.onnx` and the other files of the model),
  with the wake words in `keywords.txt`.
- Memory: about twice the model's size plus 64 MB.
- Licence: the corpus comes under its publisher's own terms of use, which are not a free licence. The block's terms
  are shown in full before the block is used, and nothing of it is built, sent or fetched before you accepted them.
- whisper.cpp's own wake-word mode (its `command` example) needs SDL2 and a microphone on the board; the framework's
  whisper.cpp build is the server only.

## What is missing

sherpa-onnx is not built by the framework yet, and a model folder needs one checked archive per component.
