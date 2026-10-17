---
title: Quantisierungsarten in RKLLM
lang: de
---
# Quantisierungsarten in RKLLM

RKLLM quantisiert Sprachmodelle beim Export für die NPU. Welche Arten zur Verfügung stehen, hängt vom Chip ab:

- **RK3588:** `w8a8` sowie die gruppierten Varianten `w8a8_g128`, `w8a8_g256` und `w8a8_g512`. Gewichte und
  Aktivierungen werden mit acht Bit gespeichert; die Gruppengröße gibt an, für wie viele Gewichte ein eigener
  Skalierungsfaktor gilt.
- **RK3576:** zusätzlich `w4a16` mit Gruppen. Vier-Bit-Gewichte halbieren den Speicherbedarf gegenüber `w8a8`,
  kosten aber etwas Genauigkeit.

Kleinere Gruppen verbessern die Genauigkeit und vergrößern das Modell leicht. Für die Kalibrierung nimmt der
Export eine JSON-Datei mit typischen Eingaben und erwarteten Ausgaben entgegen; ohne eigene Daten nutzt er einen
mitgelieferten Beispielsatz. `optimization_level` steuert, wie viel Rechenzeit der Export in die Genauigkeit
steckt.
