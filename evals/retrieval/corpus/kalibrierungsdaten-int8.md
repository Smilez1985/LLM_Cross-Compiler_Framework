---
title: Kalibrierungsdaten für die INT8-Quantisierung
lang: de
---
# Kalibrierungsdaten für die INT8-Quantisierung

Bei der INT8-Quantisierung misst der Konverter an Beispieleingaben, in welchem Wertebereich sich die Aktivierungen
jeder Schicht bewegen. Diese Kalibrierungsdaten sollten den späteren Einsatz abbilden: dieselbe Kamera, dieselben
Lichtverhältnisse, dieselben Motive.

Meist genügen 100 bis 500 Bilder. Mehr Bilder verlängern die Konvertierung, verbessern die Genauigkeit aber kaum.
Zu wenige oder untypische Bilder führen dagegen zu deutlichem Genauigkeitsverlust, etwa wenn nur Tagaufnahmen
verwendet werden, die Kamera aber auch nachts läuft.

Die Pfade stehen zeilenweise in einer Textdatei, die dem Konverter übergeben wird, bei RKNN-Toolkit2 als
`dataset` von `rknn.build()`. Relative Pfade gelten relativ zum Arbeitsverzeichnis des Konverters. Die Bilder
müssen dieselbe Vorverarbeitung durchlaufen wie im Betrieb; liegt die Normalisierung im Modell, werden sie
unverändert übergeben.
