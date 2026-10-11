---
title: GGUF quantization types at a glance
lang: en
---
# GGUF quantization types at a glance

llama.cpp stores quantized weights in blocks with their own scales; K-quants and I-quants add further parameters
per block. Sizes below are approximate for a model with seven billion parameters, without the KV cache.

| Type | Bits per weight | Size (7B) | Quality compared with F16 | Notes |
|---|---|---|---|---|
| F32 | 32.0 | 28.0 GB | reference | only for conversion and debugging |
| F16 | 16.0 | 14.0 GB | reference | usual output of the conversion script |
| BF16 | 16.0 | 14.0 GB | reference | keeps the value range of the training weights |
| Q8_0 | 8.5 | 7.4 GB | nearly lossless | good input for further quantization |
| Q6_K | 6.6 | 5.8 GB | very small loss | |
| Q5_K_M | 5.7 | 5.0 GB | small loss | |
| Q5_K_S | 5.5 | 4.8 GB | small loss | |
| Q5_0 | 5.5 | 4.8 GB | small loss | legacy type, prefer Q5_K_S |
| Q4_K_M | 4.8 | 4.2 GB | noticeable on hard tasks | the usual balance of size and quality |
| Q4_K_S | 4.6 | 4.0 GB | noticeable | |
| Q4_0 | 4.5 | 3.9 GB | noticeable | fast on ARM CPUs with repacked weights |
| IQ4_NL | 4.5 | 3.9 GB | noticeable | non-linear 4-bit grid |
| IQ4_XS | 4.3 | 3.7 GB | noticeable | smaller than Q4_K_S at similar quality |
| Q3_K_L | 4.3 | 3.7 GB | clear | |
| Q3_K_M | 3.9 | 3.4 GB | clear | |
| IQ3_M | 3.7 | 3.2 GB | clear | importance matrix recommended |
| Q3_K_S | 3.5 | 3.1 GB | strong | |
| IQ3_S | 3.4 | 3.0 GB | strong | importance matrix recommended |
| IQ3_XXS | 3.1 | 2.7 GB | strong | importance matrix required |
| Q2_K | 3.0 | 2.6 GB | very strong | |
| IQ2_M | 2.7 | 2.4 GB | very strong | importance matrix required |
| IQ2_S | 2.5 | 2.2 GB | very strong | importance matrix required |
| IQ2_XS | 2.3 | 2.0 GB | severe | importance matrix required |
| IQ2_XXS | 2.1 | 1.8 GB | severe | importance matrix required |
| IQ1_M | 1.8 | 1.5 GB | unusable for most tasks | importance matrix required |
| IQ1_S | 1.6 | 1.4 GB | unusable for most tasks | smallest type; importance matrix required |
