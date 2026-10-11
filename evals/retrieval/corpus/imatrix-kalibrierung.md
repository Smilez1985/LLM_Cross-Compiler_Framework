---
title: Importance-Matrix für niedrige Bitbreiten
lang: de
---
# Importance-Matrix für niedrige Bitbreiten

Unterhalb von etwa vier Bit pro Gewicht entscheidet die Wahl der Rundung über die Qualität. Eine Importance-Matrix
hält fest, welche Gewichte bei typischen Eingaben besonders stark wirken; `llama-quantize` rundet diese dann
genauer.

```
llama-imatrix -m model-f16.gguf -f kalibrierung.txt -o imatrix.dat --chunks 200
llama-quantize --imatrix imatrix.dat model-f16.gguf model-IQ3_XXS.gguf IQ3_XXS
```

Der Kalibrierungstext sollte zur späteren Nutzung passen: Dokumentation und Code für einen Programmierassistenten,
Gesprächstexte für einen Chat. Einige hunderttausend Token genügen. Für die IQ2- und IQ1-Typen ist die Matrix
Pflicht, für Q4_K_M und größere Typen bringt sie nur noch wenig.

Im Framework schalten `--imatrix` und `--dataset <datei.txt>` bei `llm-cli build start` diesen Schritt ein; ohne
eigene Datei wird der mitgelieferte Kalibrierungstext verwendet.
