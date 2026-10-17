---
title: RKLLM runtime and toolkit versions
lang: en
---
# RKLLM runtime and toolkit versions

RKLLM-Toolkit converts Hugging Face language models into `.rkllm` files for the NPU of the RK3588 and the
RK3576. On the board, the runtime library `librkllmrt.so` loads and runs them.

The file format changes between releases, so a model exported with one toolkit release only loads with the
runtime of the same release. A mismatch is reported when the model is loaded:

```
E RKLLM: Invalid RKLLM model version, please use the same version of toolkit and runtime
```

Either copy the matching `librkllmrt.so` to `/usr/lib` on the board and restart the application, or export the
model again with the toolkit release that matches the installed runtime. The runtime prints its version at
start-up (`I rkllm: rkllm-runtime version: ...`), and `strings /usr/lib/librkllmrt.so | grep -i version` shows it
without loading a model.

Keep the toolkit wheel, the runtime library and the demo sources of one release together, and record the release
in the build manifest, so that a model can be rebuilt with the same versions later.
