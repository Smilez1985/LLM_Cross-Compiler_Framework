---
title: NPU-Treiberversion auf Rockchip-Boards prüfen
lang: de
---
# NPU-Treiberversion auf Rockchip-Boards prüfen

Die NPU wird vom Kernel-Treiber RKNPU angesteuert. Welche Version läuft, zeigt das Debug-Dateisystem (als root):

```
cat /sys/kernel/debug/rknpu/version
```

Ist debugfs nicht eingehängt, hilft `sudo mount -t debugfs none /sys/kernel/debug`. Alternativ steht die Version
in der Kernel-Meldung beim Start: `dmesg | grep -i rknpu`.

Neuere Laufzeitbibliotheken setzen einen aktuellen Treiber voraus. Ist er zu alt, scheitert das Laden eines
Modells mit einer Meldung über eine nicht unterstützte Treiberversion. Der Treiber gehört zum Kernel des Boards
und lässt sich nicht per Paket aktualisieren; ein neueres Systemabbild des Board-Herstellers bringt ihn mit.
Mainline-Kernel ohne den Treiber des Herstellers haben keine nutzbare NPU.
