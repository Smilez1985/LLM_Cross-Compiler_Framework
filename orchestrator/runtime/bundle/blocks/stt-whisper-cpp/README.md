# Speech recognition (whisper.cpp server)

Block `stt-whisper-cpp` (kind stt) of the board bundle: whisper.cpp's `whisper-server` on 127.0.0.1.

## Models

- The program: `whisper-server` from whisper.cpp (examples/server), built for the board's architecture. The generator
  reads its ELF header and refuses a program for another architecture.
- One ggml whisper model (`model.bin` on the board, for example `ggml-base.bin` or `ggml-small.bin` from the
  whisper.cpp model list), checked by its first bytes (`lmgg`). Quantized ggml models work the same way.
- Memory: the model's size times 1.5 plus about 128 MB (tiny about 0.2 GB, base about 0.3 GB, small about 0.8 GB,
  medium about 2.3 GB).
- Licence: the OpenAI whisper models are MIT; name the licence of the model you choose.

## Variables

- `port` (8090), `threads` (0 = whisper.cpp decides), `language` (auto or a language code).
- `autostart` (no): with yes, root actions create_user, install_unit and set_cgroup (`user`, `memory_limit_mb`),
  carried out by llmcf-install.
