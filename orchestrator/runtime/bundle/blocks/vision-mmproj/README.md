# Vision (llama-server with --mmproj)

Block `vision-mmproj` (kind vision) of the board bundle: the board's self-built llama.cpp runtime (the same package
as the language model block) serving a vision language model with its multimodal projector, on 127.0.0.1 with the
runtime's key file.

## Models

- A vision language model in GGUF (for example a Qwen2-VL, Gemma 3 or SmolVLM GGUF).
- Its multimodal projector, the `mmproj` GGUF file of exactly that model (format `mmproj-gguf`). The plan reads both
  headers and refuses a pair that is not one: the projector must be a CLIP GGUF with a vision encoder
  (`general.architecture` clip, `clip.has_vision_encoder`), the model must not be; when both name their base
  (`general.basename`), it must be the same. When the projector's width (`clip.vision.projection_dim`) is not the
  model's embedding width, the plan warns: llama-server compares the two when it loads them and refuses a pair that
  does not fit.
- Memory: about 1.3 times model plus projector, plus 384 MB; the plan adds it to the bundle's total and checks it
  against the board's memory.
- Licence: name the licences of both files.
- The runtime's start script passes `--mmproj` on only with a readable local file (never an address: `--mmproj-url`
  stays forbidden). A runtime package made by an older framework is packaged again from the stored build when the
  pinned llama.cpp source is cached; otherwise the plan says to build the board's runtime.

## Variables

- `port` (8081), `ctx_size` (4096), `threads` (0 = llama.cpp decides).
- `autostart` (no): with yes, root actions create_user, install_unit and set_cgroup (`user`, `memory_limit_mb`),
  carried out by llmcf-install.
