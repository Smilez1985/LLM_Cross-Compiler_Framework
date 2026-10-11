---
title: llama.cpp auf dem Jetson Orin bauen
lang: de
---
# llama.cpp auf dem Jetson Orin bauen

JetPack 6 bringt für die Jetson-Orin-Module CUDA 12 mit. Die GPU des Orin hat die Compute Capability 8.7. Geben
Sie beim Konfigurieren nur diese Architektur an, das spart viel Übersetzungszeit:

```
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87
cmake --build build --config Release -j 6
```

Gebaut wird am einfachsten direkt auf dem Gerät oder in einem L4T-Container, der zur installierten JetPack-Version
passt. CPU und GPU teilen sich den Arbeitsspeicher; `tegrastats` zeigt, wie viel davon belegt ist.

Für Messungen schalten Sie mit `sudo nvpmodel -m 0` das stärkste Leistungsprofil ein und fixieren die Takte mit
`sudo jetson_clocks`. Ohne diese Einstellungen schwanken die Ergebnisse von Lauf zu Lauf deutlich.
