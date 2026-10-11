Version 3.0.0 · Zuletzt aktualisiert am 2026-10-11

[English](README.md) · **Deutsch**

# Kalibrierungsdaten

Text, aus dem Importance Matrices berechnet werden (llama.cpp `--imatrix`), wenn ein Build keinen eigenen
Datensatz hat.

## Dateien

| Datei | Quelldatei | sha256 der Quelle | sha256 des Texts (entpackt) |
|---|---|---|---|
| `calibration_de.txt.gz` | `combined_de_micro.parquet` | `96c7087f2e1a7108f0ee434f2d040e6ad123fa6a6cf8d8e37d669313323efb18` | `12ed14698863dab2771535d9ddf110477de5dbf8e11b528a07d0bf33678142ac` |
| `calibration_en.txt.gz` | `combined_en_micro.parquet` | `510b3ec524f8d7bccb46b3ffc6131343620892b8ceee857f0fd41a2c19d26438` | `0b9d1a768f5030cdf3c6397f504b0684eed4727a95f9d80b30191516feb82d8a` |
| `calibration_tools.txt.gz` | `tools_micro.parquet` | `6d08aea9d33c7d7565043e8dd99d0fa0a5405954db90975668124f7061686385` | `0ecef8aa3cde7691a102576d0290ae882833ec15fcfdb6552c50576d25df5486` |

## Quelle und Lizenz

- **Quelle:** [eaddario/imatrix-calibration](https://huggingface.co/datasets/eaddario/imatrix-calibration),
  Revision `e87ed55dcba9d9c3a3e41539f3e728e981b1daa4`. Die Spalte `content` jeder Parquet-Datei, unverändert,
  gespeichert als gzip-komprimierter UTF-8-Text.
- **Lizenz:** MIT (siehe Dataset Card). Das Urheberrecht liegt bei den Autoren des Datensatzes.

## Wie das Framework sie nutzt

- **Nur für GGUF.** Ein GGUF-Build mit eingeschalteter Importance Matrix (`llm-cli build start --imatrix` oder
  das Kästchen „Importance matrix (IMatrix, GGUF)“ in der GUI) nutzt diesen Text, wenn du keinen eigenen
  Datensatz angibst (`--dataset`). RKLLM- und RKNN-Builds nutzen ihn nie; sie bekommen nur deinen eigenen
  Datensatz, falls du einen angibst.
- **Vor der Nutzung geprüft.** Bevor ein Build einen Satz nutzt, vergleicht das Framework den sha256 seines Texts
  mit dem Wert oben (`orchestrator/utils/calibration.py`). Eine fehlende oder veränderte Datei wird abgelehnt.
  Der Build läuft dann ohne Importance Matrix weiter und zeigt eine Warnung, oder er bricht vor dem Image-Build
  ab, wenn ein gewählter Quantisierungstyp die Matrix braucht (`IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`).
- **Sprachmischung.** Die Einstellung `calibration_language` (in `framework.yml` im Konfigurationsordner) wählt
  die Sätze: `mixed` (Standard: Deutsch, Englisch und Tool-Nutzung), `de` (Deutsch und Tool-Nutzung) oder `en`
  (Englisch und Tool-Nutzung). Die Sätze werden in Blöcken von etwa 2.000 Zeichen abwechselnd aneinandergereiht,
  damit schon die ersten Abschnitte, die llama-imatrix liest, jeden Satz enthalten.
- **Wohin der vorbereitete Text geht.** Der gemischte Text wird in den Cache-Ordner des Frameworks geschrieben
  (`calibration/calibration_<Sprache>.txt`) und von dort an den Build übergeben.
- **Gesundheitsprüfung.** `llm-cli doctor` prüft die drei Dateien und meldet „Calibration data intact“. Fehlt
  eine oder ist sie beschädigt, sagt es dir, wie du sie aus dem Repository wiederherstellst:
  `git checkout -- data/calibration`.
