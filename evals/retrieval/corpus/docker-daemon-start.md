---
title: Docker-Dienst starten und ohne sudo nutzen
lang: de
---
# Docker-Dienst starten und ohne sudo nutzen

Läuft der Docker-Dienst nicht, meldet der Client:

```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?
```

Unter Linux starten Sie den Dienst mit `sudo systemctl start docker` und aktivieren den Autostart mit
`sudo systemctl enable docker`. Unter Windows und macOS muss Docker Desktop laufen.

Damit Sie Docker ohne sudo nutzen können, fügen Sie Ihren Benutzer der Gruppe `docker` hinzu
(`sudo usermod -aG docker $USER`) und melden sich neu an. Die Gruppe gewährt praktisch Root-Rechte auf dem
Rechner; auf gemeinsam genutzten Systemen ist rootless Docker die sicherere Wahl.

Meldet der Client stattdessen `permission denied` beim Zugriff auf den Socket, läuft der Dienst, aber der
Benutzer gehört noch nicht zur Gruppe oder hat sich seitdem nicht neu angemeldet.
