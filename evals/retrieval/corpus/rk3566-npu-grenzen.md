---
title: Grenzen der NPU des RK3566
lang: de
---
# Grenzen der NPU des RK3566

Die NPU des RK3566 und RK3568 leistet etwa ein TOPS und ist damit deutlich kleiner als die des RK3588 mit sechs
TOPS. Sie eignet sich für Bildklassifikation und Objekterkennung mit RKNN-Modellen.

Große Sprachmodelle laufen auf dieser NPU nicht: RKLLM unterstützt nur den RK3588 und den RK3576. Für kleine
Sprachmodelle bleibt auf dem RK3566 die CPU mit llama.cpp und einem stark quantisierten GGUF-Modell, etwa Q4_0.
Mit vier Cortex-A55-Kernen sind dabei nur wenige Token pro Sekunde zu erwarten; Modelle bis etwa einer Milliarde
Parameter bleiben bedienbar.

Wer Sprachmodelle auf der NPU eines Rockchip-Boards ausführen möchte, braucht deshalb ein Board mit RK3588 oder
RK3576.
