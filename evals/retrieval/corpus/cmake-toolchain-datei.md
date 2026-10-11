---
title: CMake-Toolchain-Datei für die Kreuzkompilierung
lang: de
---
# CMake-Toolchain-Datei für die Kreuzkompilierung

Für die Kreuzkompilierung mit CMake legen Sie eine Toolchain-Datei an. Darin setzen Sie `CMAKE_SYSTEM_NAME` auf
`Linux`, `CMAKE_SYSTEM_PROCESSOR` auf `aarch64` sowie `CMAKE_C_COMPILER` und `CMAKE_CXX_COMPILER` auf
`aarch64-linux-gnu-gcc` und `aarch64-linux-gnu-g++`.

Mit `CMAKE_SYSROOT` oder `CMAKE_FIND_ROOT_PATH` zeigen Sie auf die Bibliotheken des Zielsystems. Setzen Sie
`CMAKE_FIND_ROOT_PATH_MODE_LIBRARY` und `CMAKE_FIND_ROOT_PATH_MODE_INCLUDE` auf `ONLY`, damit CMake die
Bibliotheken und Header des Zielsystems findet und nicht die des Build-Rechners;
`CMAKE_FIND_ROOT_PATH_MODE_PROGRAM` bleibt auf `NEVER`, weil Werkzeuge wie Code-Generatoren auf dem Build-Rechner
laufen.

Übergeben wird die Datei beim ersten Konfigurieren mit `-DCMAKE_TOOLCHAIN_FILE=pfad/zur/datei.cmake`. Wird sie
nachträglich geändert, muss das Build-Verzeichnis gelöscht werden, weil CMake den Compiler im Cache behält.
