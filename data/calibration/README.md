# Calibration data

Text used to compute importance matrices (llama.cpp `--imatrix`) when a build has no dataset of its own.

| File | Source file | Source sha256 | Text sha256 (decompressed) |
|---|---|---|---|
| `calibration_de.txt.gz` | `combined_de_micro.parquet` | `96c7087f2e1a7108f0ee434f2d040e6ad123fa6a6cf8d8e37d669313323efb18` | `12ed14698863dab2771535d9ddf110477de5dbf8e11b528a07d0bf33678142ac` |
| `calibration_en.txt.gz` | `combined_en_micro.parquet` | `510b3ec524f8d7bccb46b3ffc6131343620892b8ceee857f0fd41a2c19d26438` | `0b9d1a768f5030cdf3c6397f504b0684eed4727a95f9d80b30191516feb82d8a` |
| `calibration_tools.txt.gz` | `tools_micro.parquet` | `6d08aea9d33c7d7565043e8dd99d0fa0a5405954db90975668124f7061686385` | `0ecef8aa3cde7691a102576d0290ae882833ec15fcfdb6552c50576d25df5486` |

- **Source:** [eaddario/imatrix-calibration](https://huggingface.co/datasets/eaddario/imatrix-calibration),
  revision `e87ed55dcba9d9c3a3e41539f3e728e981b1daa4`. The `content` column of each parquet file, unchanged,
  stored as gzip-compressed UTF-8 text.
- **License:** MIT (see the dataset card). Copyright belongs to the dataset authors.
- The framework checks the text sha256 before use (`orchestrator/utils/calibration.py`) and mixes the sets
  so the first chunks llama-imatrix reads already cover German, English and tool-use text.
