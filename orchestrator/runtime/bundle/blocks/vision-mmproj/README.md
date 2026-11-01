# Vision (llama-server with --mmproj) - template

Block `vision-mmproj` (kind vision), a documented template.

## Models

- A vision language model in GGUF (for example a Qwen2-VL, Gemma 3 or SmolVLM GGUF).
- Its multimodal projector, the `mmproj` GGUF file of exactly that model (format `mmproj-gguf`); a projector of
  another model does not work.
- Memory: about 1.3 times model plus projector, plus 384 MB.
- Licence: name the licences of both files.

## What is missing

The runtime's start script checks every option it passes on, and `--mmproj` is not one of them yet (ROADMAP row
"Vision with --mmproj as a bundle component").
