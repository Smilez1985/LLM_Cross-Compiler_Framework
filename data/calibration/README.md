Version 3.1.0 · Last updated 2026-10-29

**English** · [Deutsch](README_DE.md)

# Calibration data

Text used to compute importance matrices (llama.cpp `--imatrix`) when a build has no dataset of its own.

## Files

| File | Source file | Source sha256 | Text sha256 (decompressed) |
|---|---|---|---|
| `calibration_de.txt.gz` | `combined_de_micro.parquet` | `96c7087f2e1a7108f0ee434f2d040e6ad123fa6a6cf8d8e37d669313323efb18` | `12ed14698863dab2771535d9ddf110477de5dbf8e11b528a07d0bf33678142ac` |
| `calibration_en.txt.gz` | `combined_en_micro.parquet` | `510b3ec524f8d7bccb46b3ffc6131343620892b8ceee857f0fd41a2c19d26438` | `0b9d1a768f5030cdf3c6397f504b0684eed4727a95f9d80b30191516feb82d8a` |
| `calibration_tools.txt.gz` | `tools_micro.parquet` | `6d08aea9d33c7d7565043e8dd99d0fa0a5405954db90975668124f7061686385` | `0ecef8aa3cde7691a102576d0290ae882833ec15fcfdb6552c50576d25df5486` |

## Source and license

- **Source:** [eaddario/imatrix-calibration](https://huggingface.co/datasets/eaddario/imatrix-calibration),
  revision `e87ed55dcba9d9c3a3e41539f3e728e981b1daa4`. The `content` column of each parquet file, unchanged,
  stored as gzip-compressed UTF-8 text.
- **License:** MIT (see the dataset card). Copyright belongs to the dataset authors.

## How the framework uses it

- **Only for GGUF.** A GGUF build with the importance matrix switched on (`llm-cli build start --imatrix`, or
  the box "Importance matrix (IMatrix, GGUF)" in the GUI) uses this text when you give no dataset of your own
  (`--dataset`). RKLLM and RKNN builds never use it; they only get your own dataset, if you give one.
- **Checked before use.** Before a build uses a set, the framework compares its text sha256 with the value above
  (`orchestrator/utils/calibration.py`). A missing or modified file is refused. The build then runs without an
  importance matrix and shows a warning, or it stops before the image build if a chosen quantization type needs
  the matrix (`IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`).
- **Language mix.** The setting `calibration_language` (in `framework.yml` in the configuration folder) picks
  the sets: `mixed` (the default: German, English and tool use), `de` (German and tool use) or `en` (English and
  tool use). The sets are interleaved in blocks of about 2,000 characters, so the first chunks llama-imatrix
  reads already cover every set.
- **Where the prepared text goes.** The mixed text is written to the framework's cache folder
  (`calibration/calibration_<language>.txt`) and passed to the build from there.
- **Health check.** `llm-cli mojo` checks the three files and reports "Calibration data intact". If one is
  missing or damaged, it tells you how to restore it from the repository: `git checkout -- data/calibration`.
