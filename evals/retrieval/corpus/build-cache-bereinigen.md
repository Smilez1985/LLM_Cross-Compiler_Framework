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
```

Aufräumen, vom harmlosen zum gründlichen Schritt: `docker builder prune` entfernt den Build-Cache,
`docker image prune -a` alle Images ohne Container, `docker system prune` zusätzlich gestoppte Container und
ungenutzte Netzwerke. Das Framework erledigt den ersten Schritt mit `llm-cli doctor --fix --tier 2`.

Danach dauert der nächste Build länger, weil Basis-Images und Abhängigkeiten neu geladen werden. Liegt der
Modellordner auf derselben Partition, lohnt es sich, ihn auf ein größeres Laufwerk zu verlegen.
