Version 3.4.0 · Stand 2026-11-10

[English](README.md) · **Deutsch**

# Bewertungstext

Der feste Text, auf dem Auto-Tuning quantisierte Modelle misst (`llama-perplexity --kl-divergence`): Jeder
Kandidatentyp wird auf genau diesem Text mit dem Originalmodell verglichen, nie auf dem Kalibrierungstext, aus dem
seine Importance Matrix berechnet wurde.

## Dateien

| Datei | Quelle | sha256 der Quelldatei | sha256 des Texts (entpackt) |
|---|---|---|---|
| `evaluation_en.txt.gz` | Arthur Conan Doyle, *The Adventures of Sherlock Holmes* (Project-Gutenberg-E-Book #1661) | `922e2a12ccb43a4c9544c260b2166c6ad2097aeb5957faeee113f173bb857cd0` | `5f2d1122350d223a2885ad44a1deab2754a8af719bd1e717a905024e80a0ffd8` |
| `evaluation_de.txt.gz` | Franz Kafka, *Die Verwandlung* (Project-Gutenberg-E-Book #22367) | `359d3f5983c812393f4bd7ee49a350ffffc7d476015e58d6b84b6c848dd2b0e9` | `894ce3e0b2e1ccb84423c80a068921592cd663b4ec252c3f45eadace7f63779b` |

## Quelle und Lizenz

- **Gemeinfrei.** Arthur Conan Doyle starb 1930 und Franz Kafka 1924; beide Werke sind in der Europäischen Union und
  in den Vereinigten Staaten gemeinfrei.
- **Aufbereitet aus** den Textdateien von Project Gutenberg, geholt am 2026-11-10 (die sha256 der Quelle oben): nur
  der Text zwischen Start- und Endmarke, ohne Kopf, Lizenz und Marke von Project Gutenberg, Absätze zu je einer Zeile
  verbunden, Unterstriche der Hervorhebung und Kapitelziffern entfernt, die ersten etwa 64.000 Zeichen jedes Werks.
  Kein Wort der Werke wurde geändert.
- **Getrennt vom Kalibrierungstext**: Keine Folge von 60 Zeichen dieses Texts kommt in `data/calibration` vor
  (`tests/test_tuning.py` prüft das).

## Wie das Framework ihn nutzt

- **Nur für Auto-Tuning** (`llm-cli build tune start`, der Reiter Auto-Tuning): Die beiden Sätze werden in Blöcken
  von etwa 2.000 Zeichen verschränkt (Deutsch zuerst, wie in der Kalibrierungsmischung), damit schon die ersten
  Chunks, die llama-perplexity liest, beide Sprachen enthalten, und mit LF-Zeilenenden in den Arbeitsordner des Laufs
  geschrieben.
- **Vor der Nutzung geprüft.** Die sha256 des Texts jedes Satzes wird mit dem Wert oben verglichen
  (`orchestrator/utils/evaluation_text.py`); eine fehlende oder geänderte Datei verweigert den Tuning-Lauf, bevor
  etwas heruntergeladen wird. Aus dem Repository wiederherstellen: `git checkout -- data/evaluation`.
