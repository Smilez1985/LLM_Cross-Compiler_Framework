---
title: Starting a build from the command line
lang: en
---
# Starting a build from the command line

`llm-cli build start` converts and quantizes a model for a target. The model is a Hugging Face repository or a
local folder; the target is a folder name under `targets/`.

```
llm-cli build start --model <repository or path> --target Rockchip --format gguf \
    --board rk3588 --quantization Q4_K_M --follow
```

- `--format` chooses the output format the target supports, for example `gguf`, `rkllm` or `rknn`.
- `--board` selects the board within the target family (`rk3588`, `rk3576`, `rk3566`); the target's capabilities
  decide which formats and quantizations each board accepts, and an unsupported combination is refused before
  anything is built.
- `--imatrix` together with `--dataset <file.txt>` computes an importance matrix from your own calibration text.
- `--gpu` passes the GPU into the build container; `--output-dir` changes where the artifacts are written.
- `--follow` shows the build log live.

`llm-cli targets list` shows the installed targets, and `llm-cli build status <request-id>` (or `--all`) reports
progress and errors of running and finished builds.
