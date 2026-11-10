Version 3.4.0 · Last updated 2026-11-10

**English** · [Deutsch](README_DE.md)

# Evaluation text

The fixed text Auto-Tuning measures quantized models on (`llama-perplexity --kl-divergence`): every candidate type is
compared with the original model on exactly this text, never on the calibration text its importance matrix was
computed from.

## Files

| File | Source | Source file sha256 | Text sha256 (decompressed) |
|---|---|---|---|
| `evaluation_en.txt.gz` | Arthur Conan Doyle, *The Adventures of Sherlock Holmes* (Project Gutenberg eBook #1661) | `922e2a12ccb43a4c9544c260b2166c6ad2097aeb5957faeee113f173bb857cd0` | `5f2d1122350d223a2885ad44a1deab2754a8af719bd1e717a905024e80a0ffd8` |
| `evaluation_de.txt.gz` | Franz Kafka, *Die Verwandlung* (Project Gutenberg eBook #22367) | `359d3f5983c812393f4bd7ee49a350ffffc7d476015e58d6b84b6c848dd2b0e9` | `894ce3e0b2e1ccb84423c80a068921592cd663b4ec252c3f45eadace7f63779b` |

## Source and license

- **Public domain.** Arthur Conan Doyle died in 1930 and Franz Kafka in 1924; both works are in the public domain in
  the European Union and the United States.
- **Prepared from** the plain-text files of Project Gutenberg as fetched on 2026-11-10 (the source sha256 above):
  the text between the start and end markers only, without the Project Gutenberg header, licence and trademark,
  paragraphs joined into one line each, emphasis underscores and chapter numerals removed, the first 64,000 or so
  characters of each work. No word of the works was changed.
- **Distinct from the calibration text**: no run of 60 characters of this text occurs in `data/calibration`
  (`tests/test_tuning.py` checks it).

## How the framework uses it

- **Only for Auto-Tuning** (`llm-cli build tune start`, the Auto-Tuning tab): the two sets are interleaved in blocks
  of about 2,000 characters (German first, as in the calibration mix), so the first chunks llama-perplexity reads
  already hold both languages, and written with LF line ends to the run's work folder.
- **Checked before use.** The text sha256 of each set is compared with the value above
  (`orchestrator/utils/evaluation_text.py`); a missing or modified file refuses the tuning run before anything is
  downloaded. Restore it from the repository: `git checkout -- data/evaluation`.
