# Language model (llama-server)

Block `llm-llama-server` (kind llm) of the board bundle. It runs the board's self-built llama.cpp runtime package
(the same tarball deploy way 2 installs, with its `SHA256SUMS` and start script) with one model.

## Models

- One GGUF model (`model.gguf` on the board), checked by its first bytes (`GGUF`). Any llama.cpp model works; the
  quantization decides the size.
- Memory: the model's size times 1.2 plus about 256 MB must fit into the board's memory (the probe's `RAM_TOTAL_MB`,
  with a reserve for the system); the generator refuses a model that does not fit.
- Licence: name the model's licence (an SPDX identifier such as `apache-2.0`, or the model family's own word such as
  `llama3`). It stands in the manifest and in the plan you confirm.
- The runtime package is the board's self-built llama.cpp runtime (deploy way 2, built in the Boards window); the
  bundle reuses it and never builds a second llama.cpp.

## Variables

- `port` (8080): the loopback port; the server never listens on another interface.
- `ctx_size` (4096), `threads` (0 = llama.cpp decides): passed to the start script, which checks them again.
- `autostart` (no): with yes, the bundle lists the root actions create_user, install_unit and set_cgroup with
  `user` (llmcf) and `memory_limit_mb` (2048); llmcf-install carries them out.
