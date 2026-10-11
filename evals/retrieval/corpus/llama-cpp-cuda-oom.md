---
title: "llama.cpp: CUDA out of memory"
lang: en
---
# CUDA out of memory in llama.cpp

When `llama-server` or `llama-cli` stops with `CUDA error: out of memory` or `failed to allocate CUDA buffer`,
the model weights, the KV cache and the compute buffers together do not fit into the VRAM. In order of impact:

1. Offload fewer layers: `--n-gpu-layers` (`-ngl`) sets how many layers live on the GPU; the rest run on the CPU.
2. Shorten the context: `-c 4096` instead of the model's maximum. The KV cache grows linearly with the context.
3. Quantize the KV cache: `--cache-type-k q8_0 --cache-type-v q8_0` roughly halves it. A quantized V cache needs
   flash attention (`-fa`).
4. Lower the batch sizes (`-b`, `-ub`); this reduces the peak memory of prompt processing.
5. Use a smaller quantization of the model, for example Q4_K_M instead of Q6_K.

Check the free memory with `nvidia-smi` before starting; a desktop session or another process can hold several
gigabytes of VRAM.
