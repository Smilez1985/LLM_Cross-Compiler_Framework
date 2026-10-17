---
title: "Speicherplatz: Docker-Build-Cache aufräumen"
lang: de
---
# Speicherplatz: Docker-Build-Cache aufräumen

Bricht ein Build mit `no space left on device` ab, ist meist die Partition voll, auf der Docker seine Images und
den Build-Cache ablegt (unter Linux `/var/lib/docker`). Konvertierte Modelle, Zwischenstände in F16 und alte
Images belegen schnell mehrere Dutzend Gigabyte.

Überblick verschaffen:

```
docker system df
df -h /var/lib/docker
llm-cli doctor
```

`llm-cli doctor` nennt den Ordner, in dem Docker seine Images ablegt, und wie viel die Build-Images des Frameworks
belegen. Das Framework löscht nie von selbst ein Image und leert nie den Build-Cache: der Cache gehört jedem
Projekt auf dem Rechner. `llm-cli doctor --fix --tier 2` (oder Tools > Framework Build Images... in der GUI)
listet nur die eigenen Build-Images des Frameworks mit Name, ID und Größe; nichts ist angehakt, gelöscht wird nur,
was du auswählst und bestätigst.

`docker builder prune` leert den Build-Cache für jedes Projekt auf diesem Computer. Das ist deine eigene
Entscheidung, das Framework tut es nicht. Danach dauert der nächste Build länger, weil Basis-Images und
Abhängigkeiten neu geladen werden. Liegt der Modellordner auf derselben Partition, lohnt es sich, ihn auf ein
größeres Laufwerk zu verlegen.
