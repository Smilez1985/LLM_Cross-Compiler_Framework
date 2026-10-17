---
title: Ein Modul für eine neue Zielplattform anlegen
lang: de
---
# Ein Modul für eine neue Zielplattform anlegen

Jede Zielplattform ist ein Ordner unter `targets/` mit Dockerfile, Build-Skripten und einer Beschreibung der
unterstützten Formate und Boards.

1. Auf dem Gerät erfasst `llm-cli scan` CPU, Befehlssatzerweiterungen, Speicher und Beschleuniger und schreibt
   sie in die Datei `target_hardware_config.txt`.
2. Auf dem Build-Rechner schlägt `llm-cli module generate-ai target_hardware_config.txt` mit Ditto Architektur,
   SDK, Compiler-Flags und CMake-Optionen vor und legt das Modul an. Ohne KI geht es mit
   `llm-cli module create --name <name> --arch aarch64 --sdk <sdk>`; die Werte trägt man dann selbst nach.
3. In der GUI führt der Modul-Assistent durch dieselben Schritte.

Prüfen Sie das erzeugte Dockerfile und die Skripte vor dem ersten Build, und tragen Sie in der
Fähigkeitsbeschreibung nur Formate und Quantisierungen ein, die auf dem Board wirklich getestet wurden.
`llm-cli targets validate <name>` meldet fehlende Dateien und Platzhalter.
