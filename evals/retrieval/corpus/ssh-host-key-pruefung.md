---
title: Host-Schlüssel beim ersten SSH-Deployment prüfen
lang: de
---
# Host-Schlüssel beim ersten SSH-Deployment prüfen

Beim ersten Deployment auf ein Gerät zeigt das Framework den SHA256-Fingerabdruck des SSH-Host-Schlüssels an,
den das Gerät präsentiert, und fragt, ob Sie ihm vertrauen. Vergleichen Sie ihn mit dem Fingerabdruck, den das
Gerät selbst anzeigt, am besten über eine lokale Konsole oder die serielle Schnittstelle:

```
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Erst nach Ihrer Bestätigung wird der Schlüssel gespeichert, in einer eigenen `known_hosts`-Datei im
Benutzerverzeichnis des Frameworks. Ändert sich der Schlüssel eines bekannten Geräts später, bricht das Framework
die Verbindung ab, statt den neuen Schlüssel stillschweigend zu übernehmen.

Für Skripte gibt es `llm-cli deploy run ... --trust-fingerprint SHA256:<wert>`: Das Gerät wird nur vertraut,
wenn sein Schlüssel genau diesen Fingerabdruck hat. Ein Passwort wird nur bei der ersten Verbindung verwendet, um
den SSH-Schlüssel des Frameworks einzurichten, und nie gespeichert.
