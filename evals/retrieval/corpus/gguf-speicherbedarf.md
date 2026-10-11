---
title: Speicherbedarf von GGUF-Modellen
lang: de
---
# Speicherbedarf von GGUF-Modellen

Der Speicherbedarf eines GGUF-Modells ergibt sich grob aus der Parameterzahl mal Bits pro Gewicht, geteilt durch
acht. Ein Modell mit sieben Milliarden Parametern braucht in Q4_K_M also etwa vier bis viereinhalb Gigabyte.

Dazu kommt der KV-Cache, der mit der Kontextlänge wächst: Bei 8192 Token Kontext kann er je nach Modell ein bis
zwei Gigabyte zusätzlich belegen. Ein quantisierter KV-Cache halbiert diesen Anteil ungefähr.

Auf einem Rechner mit acht Gigabyte Arbeitsspeicher bleibt mit einem 7B-Modell wenig Luft für Betriebssystem und
Browser; ein Modell mit drei bis vier Milliarden Parametern ist dort die bessere Wahl. Ob das Modell passt, zeigt
sich am zuverlässigsten beim ersten Start: llama.cpp meldet die Größe der Gewichte und des KV-Caches im Log.
