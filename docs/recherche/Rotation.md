Version 3.4.0 · Last updated 2026-11-10

# Rotation before quantization: research

[Deutsche Fassung](Rotation_DE.md)

Research date: 2026-11-10. All sources were accessed on that date. They are listed at the end and cited as [n].
Facts come from the cited source, from reading llama.cpp's code at the framework's pin (tag b11429, commit
`d81235049384534c167caea52b85a694f6103d14`), or from our own measurement in section 5, whose setup is given in full.
Text marked **[Inference]** is our own conclusion. Text marked **[Unconfirmed]** could not be checked against a
primary source or a measurement.

This is the groundwork for the ROADMAP row "Rotation stage before quantization": whether rotating a model's weights
before quantization (QuaRot, SpinQuant) makes the 4-bit models llmcf builds better on small boards, for which formats,
and at what cost.

## Contents

1. [The methods](#1-the-methods)
2. [Which rotations need the runtime and which fold into the weights](#2-which-rotations-need-the-runtime-and-which-fold-into-the-weights)
3. [What the formats llmcf builds support](#3-what-the-formats-llmcf-builds-support)
4. [Published measurements](#4-published-measurements)
5. [Our measurement: GGUF with an importance matrix, rotated and not](#5-our-measurement-gguf-with-an-importance-matrix-rotated-and-not)
6. [What this means for llmcf](#6-what-this-means-for-llmcf)
7. [Sources](#7-sources)

---

## 1. The methods

**Computational invariance.** In a transformer whose normalisation is RMSNorm, the residual stream can be multiplied
by an orthogonal matrix Q without changing the model's output: the norm of a vector does not change under Q, the
scale vector of each RMSNorm is first multiplied into the weights of the linear layers that read the norm's output,
every weight that reads the residual stream is multiplied by Q from the right (W Q), every weight that writes into it
by Q transposed from the left (Qᵀ W), and the token embedding is rotated as well (E Q) [1, section 3; SliceGPT, which
QuaRot builds on]. In float the model computes the same function; only the numbers in the weight matrices change. A
random Hadamard matrix spreads a large value of one channel over all channels, so a matrix with outliers becomes one
with an even spread of values, which a uniform quantization grid fits better [1].

**QuaRot** [1] (Ashkboos et al., 2024; code under Apache-2.0 [2]) uses random Hadamard rotations without training. Its
aim is 4-bit weights *and* 4-bit activations and KV cache (W4A4KV4) on GPUs with INT4 kernels; it reports at most
0.63 perplexity points lost on Llama 2 at W4A4KV4. The model is changed in about 5 minutes on one GPU for a 70B model
[1].

**SpinQuant** [3] (Liu et al., 2024) learns the rotations instead: it optimises them on the Stiefel manifold (Cayley
SGD) over 800 WikiText-2 samples for 100 iterations with the quantized network in the loop. The paper reports about 13,
18 and 30 minutes for Llama 3 1B, 3B and 8B and 3.5 hours for Llama 2 70B; the code needs PyTorch with CUDA and the
`fast-hadamard-transform` package [4]. The repository is under **CC BY-NC 4.0** [5] (non-commercial), so its code cannot
be taken into llmcf, which is MIT. Meta published 4-bit Llama 3.2 1B and 3B SpinQuant models for ExecuTorch (4-bit
group-wise weights with 8-bit dynamic activations), not as GGUF [4].

## 2. Which rotations need the runtime and which fold into the weights

Both papers name four rotations [1] [3]:

| Rotation | Where | Folds into the weights? |
|---|---|---|
| R1 | the residual stream (hidden size) | Yes: embedding, the linear layers reading and writing the residual stream, the output head. The RMSNorm scales are multiplied into the reading layers first and set to 1. |
| R2 | per attention head, between value projection and output projection | Yes: `W_v` rows and `W_o` columns per head. |
| R3 | queries and keys after RoPE (for a 4-bit KV cache) | No: an online Hadamard transform at inference. |
| R4 | the input of the down projection (for 4-bit activations) | No: an online Hadamard transform at inference (QuaRot also applies the cross-head part of R2 online). |

SpinQuant's variant without online transforms (SpinQuant_noHad) uses only R1 and R2 [3]. **For weight-only
quantization, which is what GGUF k-quants and IQ types are, only R1 and R2 are useful, and both fold into the weights:
the runtime needs no change.** One property costs memory: a model whose output head shares the embedding matrix (tied
embeddings, for example Llama 3.2 1B and 3B, SmolLM2) must get its own output head, because the head takes the final
RMSNorm scale and the embedding does not **[Inference, checked in section 5: the rotated file is larger]**.

## 3. What the formats llmcf builds support

**GGUF / llama.cpp.** An R1+R2-rotated Llama model is an ordinary Llama model: llama.cpp loads it unchanged
(section 5 measures it equal to the original in float). llama.cpp has no Hadamard transform of the weights' inputs at
inference; a third-party tool that rotates GGUF weights needs a fork of llama.cpp that applies the transform at runtime
[6]. Since PR #21038 (merged 2026-04-01) llama.cpp rotates queries, keys and values with block Hadamard matrices when
the KV cache is quantized, without any change to the model file, and switches it off only with the environment
variable `LLAMA_ATTN_ROT_DISABLE` [7]; at the pin this is in `src/llama-kv-cache.cpp` (on when the cache type is
quantized and the head size is a multiple of 64). So the R3 part, the only part that matters for a quantized KV cache,
is already done by the runtime llmcf ships, for every model. k-quants hold their own scale per block of 16 or 32 weights
inside super-blocks of 256 [8], and with an importance matrix the quantizer weights each column's error by how large
that column's activations are on calibration text [9].

**RKLLM (Rockchip NPU).** The RKLLM-Toolkit takes a Hugging Face model and quantizes it itself (w4a16 with GRQ,
w8a8, group-wise variants, GDQ since 1.1.0 [10]). Its changelog up to 1.3.1 names no rotation option [10]. A rotated
Llama model is an ordinary Hugging Face Llama model (with `tie_word_embeddings: false` when the head was untied), so
the toolkit would accept it. Whether it helps there cannot be checked without the closed toolkit and a board: w8a8
quantizes activations, where rotations help in principle (the papers' main case), but at 8 bits the gain the papers
show is small, and RKLLM's own algorithms and calibration may already cover it **[Unconfirmed]**.

**RKNN (vision).** ONNX vision and audio models; the method is for transformer language models with RMSNorm and does
not apply.

## 4. Published measurements

The papers' weight-only numbers use **one scale per output channel** (no groups), the setting where an outlier hurts
most:

| Llama 2 7B, WikiText-2 perplexity | FP16 | RTN W4 | QuaRot-RTN W4 | GPTQ W4 | QuaRot-GPTQ W4 |
|---|---|---|---|---|---|
| QuaRot, Table 7 [1] | 5.47 | 6.99 | 6.76 | 8.25 | 5.60 |

SpinQuant reports for W4A16 on Llama 2 7B with RTN: random Hadamard 6.9, learned 5.5, against 5.5 in float [3, Table 4].

These numbers do not carry over to GGUF. With small groups each block has its own scale, so one outlier spoils only
its own block: the analysis of microscaling FP4 formats shows that a small group size "provably neutralizes traditional
outlier mitigation techniques" [11], and a transform that flattens values within a group is a different goal from the
one an energy-concentrating quantizer has; which one wins depends on the format [12]. The only published GGUF
measurement we found rotates the weights of Qwen3-4B and 8B and runs them with a modified llama.cpp; at Q4_K_M and
Q3_K_M the change was +2.6 % and −2.7 % (and −5.4 % / +0.8 %) perplexity, which the author treats as noise, with a
consistent gain only at Q2_K; no importance matrix was used [6]. We found no measurement of rotated weights against
importance-matrix k-quants, which is llmcf's default below 5 bits per weight.

## 5. Our measurement: GGUF with an importance matrix, rotated and not

**Setup.** llmcf's ARM64 build image (llama.cpp at the pin b11429; `llama-quantize`, `llama-imatrix`,
`llama-perplexity` built for the build machine, x86-64 with AVX2, 4 threads). Models: SmolLM2-360M (Llama
architecture, hidden size 960, tied embeddings, Apache-2.0) and Llama 3.2 1B (hidden size 2048, tied embeddings). Each
converted with the image's `convert_hf_to_gguf.py` to F16. Rotation: our own implementation of R1+R2 on the F16 GGUF
with numpy and gguf-py (RMSNorm scales fused, embedding and head rotated, the head untied, R2 per head with a
normalised Hadamard matrix of the head size; R1 a random-sign Hadamard matrix for a power-of-two hidden size, else a
random orthogonal matrix from a QR decomposition, seed 0). Importance matrix: `llama-imatrix` over 100 chunks of
llmcf's shipped calibration text (the mixed set of `data/calibration`, as a build does), computed separately on the
original and on the rotated model. Quantization: `llama-quantize --imatrix`. Evaluation: `llama-perplexity` on the
WikiText-2 test set (SmolLM2: 80 chunks of 512 tokens, Llama 3.2 1B: 20 chunks), KL divergence of each quantized
model's token distribution against the original F16 model's (`--kl-divergence-base`). Lower KLD is better; it is the
measure llama.cpp's own quantization work uses beside perplexity [7] [9].

**Invariance.** The rotated F16 model gives the same distribution as the original: SmolLM2-360M mean KLD 0.000005,
same top token 99.88 % (the rest is F16 rounding of the rotated weights); Llama 3.2 1B: mean KLD 0.000002, same
top token 99.92 %, perplexity 11.105 against 11.105.

**Results.**

KL divergence to the F16 model (lower is better), perplexity in brackets, file size in MB (10⁶ bytes). "Rotated" gives
the token embedding the same type the original's tied embedding gets (Q6_K for Llama 3.2 1B, Q8_0 for SmolLM2), so
the comparison is of the rotation alone; "rotated, defaults" is what `llama-quantize` makes of the rotated file without
that option (the untied embedding then gets the type of the other layers).

| Model (F16 perplexity) | Type, with importance matrix | Original | Rotated | Rotated, defaults | Size original → rotated |
|---|---|---|---|---|---|
| Llama 3.2 1B (11.105) | Q4_K_M | **0.0407** (11.58) | 0.0628 (11.85) | 0.0643 (11.85) | 808 → 1023 |
| Llama 3.2 1B | IQ4_XS | **0.0498** (11.67) | 0.0715 (11.95) | | 743 → 959 |
| Llama 3.2 1B | Q4_0 | **0.0730** (11.93) | 0.1098 (12.35) | 0.1131 (12.40) | 773 → 988 |
| SmolLM2-360M (12.59) | Q4_K_M ¹ | **0.0225** (12.87) | 0.0450 (13.15) | 0.0455 (13.16) | 271 → 321 |
| SmolLM2-360M | IQ4_XS ¹ | **0.0546** (13.29) | 0.2090 (15.58) | 0.2112 (15.62) | 227 → 277 |
| SmolLM2-360M | Q4_0 | **0.0885** (13.75) | 0.2408 (16.04) | | 230 → 280 |
| SmolLM2-360M | Q4_0 without importance matrix | 0.1395 (14.49) | 0.3236 (17.35) | | 229 → 279 |

¹ SmolLM2's hidden size 960 is no multiple of 256, so `llama-quantize` stores most of its matrices as Q5_0 or IQ4_NL
instead of the K and XS types; Llama 3.2 1B has none of these fallbacks.

In every pair the rotated model is clearly worse, far beyond the measurement's error (the standard error of each
mean KLD is at most 0.0028), and it is about 27 % larger for Llama 3.2 1B and 18 to 22 % for SmolLM2, because the tied
output head becomes a matrix of its own.

**Why [Inference, from the numbers].** The importance matrix tells the quantizer that a few columns of each weight
matrix meet very large activations and must be kept exact. The largest importance value of an input column of the
attention is about 42 times the mean in Llama 3.2 1B and about 330 times in SmolLM2-360M (median over the layers;
23 and 93 times for the inputs of the FFN). After R1 it is 3.4 and 7 times the mean: the rotation spreads the
activation outliers over all channels, which is its purpose, and in doing so it takes away the leverage the importance
matrix had (the input of the down projection, which R1 does not touch, keeps its 342 and 34 times). The weight
outliers the rotation removes were already contained by the scale each block of 16 or 32 weights has. The loss of the
importance matrix's leverage is not the whole explanation: on SmolLM2 the rotated Q4_0 is worse without an importance
matrix as well (0.324 against 0.139). We did not measure what else is at work.

## 6. What this means for llmcf

- **GGUF (the format of every CPU build, ARM64 and Rockchip):** an offline R1+R2 rotation needs no runtime change and
  is cheap to compute (numpy, minutes on a CPU), but against llmcf's default, importance-matrix k-quants and IQ types,
  it made the 4-bit models worse in our measurement (1.4 to 3.8 times the KL divergence to the F16 model; on SmolLM2 also
  without an importance matrix), and larger when the model ties its embeddings. It gives no real
  gain, so llmcf does not build it for GGUF. The rotation that helps GGUF at inference, the Hadamard rotation of a
  quantized KV cache, is already applied by llama.cpp at the pin, for every model.
- **SpinQuant:** needs a GPU training loop and its code is non-commercial; even if the learned rotation helped
  per-channel quantization, the k-quant result above gives no reason to expect a gain over importance-matrix k-quants.
- **RKLLM w8a8:** the one format where a rotation could plausibly help (activation quantization), but it can only be
  judged with Rockchip's toolkit and a board; until a measurement shows a gain there it stays a planned, narrowed item.
- **Not verifiable here:** larger Llama models (7B and up), other model families, and quantization below 4 bits
  (Q2_K, IQ2/IQ3), where the third-party measurement saw a gain without an importance matrix [6].

## 7. Sources

1. Ashkboos et al., "QuaRot: Outlier-Free 4-Bit Inference in Rotated LLMs", arXiv:2404.00456, https://arxiv.org/abs/2404.00456 (Table 7: weight-only quantization)
2. https://github.com/spcl/QuaRot (LICENSE: Apache-2.0)
3. Liu et al., "SpinQuant: LLM quantization with learned rotations", arXiv:2405.16406, https://arxiv.org/abs/2405.16406 (Table 4, Table 17, optimisation times)
4. https://github.com/facebookresearch/SpinQuant (README: requirements, ExecuTorch export, released Llama 3.2 models)
5. https://github.com/facebookresearch/SpinQuant/blob/main/LICENSE (CC BY-NC 4.0)
6. https://github.com/christopherrobertbrooks-tech/volta-hadamard (README: GGUF weight rotation with a llama.cpp fork; Qwen3 measurements)
7. https://github.com/ggml-org/llama.cpp/pull/21038 ("llama : rotate activations for better quantization", merged 2026-04-01)
8. https://github.com/ggml-org/llama.cpp/pull/1684 (k-quants)
9. https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md (importance matrix, perplexity and KLD)
10. https://github.com/airockchip/rknn-llm/blob/main/CHANGELOG.md (RKLLM 1.0.0 to 1.3.1)
11. Egiazarian et al., "Bridging the Gap Between Promise and Performance for Microscaling FP4 Quantization", arXiv:2509.23202, https://arxiv.org/abs/2509.23202
12. Jokar, "Transforms for LLM Quantization: The Great Inversion and Format Co-Design", arXiv:2608.25188, https://arxiv.org/abs/2608.25188
13. The model cards of SmolLM2-360M and Llama 3.2 1B on Hugging Face (architecture, licence); https://huggingface.co/datasets/ggml-org/ci (WikiText-2 raw, the file llama.cpp's scripts fetch)
