---
title: "Raspberry Pi 5: Drosselung bei Dauerlast"
lang: de
---
# Raspberry Pi 5: Drosselung bei Dauerlast

Der Raspberry Pi 5 senkt den Takt seiner CPU ab etwa 80 bis 85 Grad Celsius. Bei längeren Inferenzläufen ist ein
aktiver Kühler deshalb Pflicht, sonst sinkt die Token-Rate schon nach wenigen Minuten deutlich.

Auch die Stromversorgung bremst: Ein Netzteil mit weniger als 5 Volt und 5 Ampere führt unter Last zu
Unterspannung, und der Pi drosselt dann ebenfalls. Das offizielle 27-Watt-Netzteil vermeidet das.

Den Zustand prüfen:

```
vcgencmd measure_temp
vcgencmd get_throttled
```

`get_throttled` liefert `throttled=0x0`, wenn nie gedrosselt wurde. Gesetzte Bits zeigen Unterspannung, eine
Taktbegrenzung oder das Erreichen der Temperaturgrenze an, jeweils aktuell oder seit dem Start.
