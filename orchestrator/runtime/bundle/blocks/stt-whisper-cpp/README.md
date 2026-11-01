# Speech recognition (whisper.cpp server)

Block `stt-whisper-cpp` (kind stt) of the board bundle: whisper.cpp's `whisper-server` on 127.0.0.1, built by the
framework for this board.

## Models

- The program comes from the framework, never from a file you pick: `whisper-server` (whisper.cpp's examples/server)
  is built from the pinned whisper.cpp commit (`configs/llama_runtime.yml`, `whisper_cpp.source`) for the board's
  architecture, C library and CPU features from its probe, in the builder container without network, by the same
  self-build path as llama.cpp. It is stored with its record (build ID, plan, file hashes, the format check) and
  packed with whisper.cpp's MIT licence text. When it is not built yet, the plan says so and building the bundle
  offers to build it (after your yes). A whisper-server you built yourself goes into the block
  `stt-whisper-cpp-program` instead.
- One ggml whisper model (`model.bin` on the board, for example `ggml-base.bin` or `ggml-small.bin` from the
  whisper.cpp model list), checked by its first bytes (`lmgg`). Quantized ggml models work the same way.
- Memory: the model's size times 1.5 plus about 128 MB (tiny about 0.2 GB, base about 0.3 GB, small about 0.8 GB,
  medium about 2.3 GB).
- Licence: the OpenAI whisper models are MIT; name the licence of the model you choose.
- Wake word: whisper.cpp's own wake-word mode is its `command` example, which needs SDL2 and a microphone on the
  board; the server has none. The bundle does not offer a wake word yet.

## Variables

- `port` (8090), `threads` (0 = whisper.cpp decides), `language` (auto or a language code).
- `autostart` (no): with yes, root actions create_user, install_unit and set_cgroup (`user`, `memory_limit_mb`),
  carried out by llmcf-install.
- The server never gets `--convert` (it runs ffmpeg through the shell), `--public` (it serves a folder over HTTP) or
  another `--host` than 127.0.0.1: the plan refuses them.
