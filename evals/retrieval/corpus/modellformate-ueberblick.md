---
title: Welches Modellformat für welches Ziel?
lang: de
---
# Welches Modellformat für welches Ziel?

- **GGUF** ist das Format von llama.cpp. Es läuft auf CPUs aller Architekturen und auf GPUs über CUDA, Vulkan,
  ROCm oder Metal. Für Sprachmodelle auf Boards ohne passende NPU ist es die erste Wahl.
- **RKLLM** bringt Sprachmodelle auf die NPU des RK3588 und RK3576.
- **RKNN** ist für Bild- und Audiomodelle auf den NPUs der Rockchip-Chips gedacht, vom RK3566 bis zum RK3588.
- **ONNX** dient meist als Zwischenformat: Die NPU-Konverter lesen ONNX-Dateien, und ONNX Runtime führt sie auf
  dem Host aus, um Ergebnisse zu vergleichen.

Welche Formate und Quantisierungen eine Zielplattform je Board anbietet, steht in ihrer Fähigkeitsbeschreibung.
Das Framework prüft jede Build-Anfrage dagegen und lehnt Kombinationen ab, die das Board nicht unterstützt, zum
Beispiel RKLLM auf dem RK3566.
