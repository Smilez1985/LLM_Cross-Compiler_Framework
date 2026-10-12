Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md#unreleased) · Zuletzt aktualisiert am 2026-10-12

[English](Container_Setup.md) · **Deutsch**

# Das Framework in einem Container

Diese Anleitung ist für den Betrieb des Frameworks in einem Container, den du selbst baust, zum Beispiel auf einem
Server, einem NAS oder einem Proxmox-Host. **Kein Framework-Image wird veröffentlicht oder geladen.** Ein fertiges
Image müsste für jedes Update neu gebaut und gepflegt werden; deshalb baust du das Image auf deinem eigenen Computer.
Der Builder führt im Container dieselbe Installation aus wie auf einem Debian-Computer (`install.sh`), aus genau den
Dateien, die das Repository ausliefert. Nichts wird irgendwohin hochgeladen.

Auf einem Desktop-PC ist die [normale Installation](../README_DE.md#installation) einfacher. Damit Ditto das
Framework auf einem anderen Computer nutzen kann, braucht es ebenfalls keinen Container ([Ditto und ein Framework auf
einem anderen Computer](Features_DE.md#ditto-und-ein-framework-auf-einem-anderen-computer)).

## Inhalt

1. [Was du brauchst](#1-was-du-brauchst)
2. [Das Image bauen](#2-das-image-bauen)
3. [Starten und einmal einrichten](#3-starten-und-einmal-einrichten)
4. [KI-Clients: der MCP-Server](#4-ki-clients-der-mcp-server)
5. [Wo deine Daten liegen](#5-wo-deine-daten-liegen)
6. [Die GUI (optional)](#6-die-gui-optional)
7. [Aktualisieren](#7-aktualisieren)
8. [Grenzen](#8-grenzen)
9. [Die Dateien](#9-die-dateien)

## 1. Was du brauchst

- Linux mit **Docker Engine 23 oder neuer** und BuildKit (`docker buildx`) sowie **git**.
- Einen Klon dieses Repositorys (das Image wird aus den Dateien gebaut, die `git ls-files` auflistet).
- **Internetzugang beim Bauen:** das Debian-Basis-Image (per Digest festgelegt), Debian-Pakete und die festgelegten
  Python-Pakete von PyPI. Im Betrieb braucht der Container das Internet nur für das, was das Framework selbst lädt
  (Modelle, KI-Anbieter).
- Etwa 1,5 GB Speicherplatz für das Image, dazu deine Modelle und Builds im Datenvolume.

## 2. Das Image bauen

```bash
make build                          # oder: scripts/build_container_image.sh
```

Was passiert:

1. Der Builder prüft Docker, BuildKit und git und bricht mit einer klaren Meldung ab, wenn etwas fehlt (Exit-Code 2).
2. Er kopiert genau die Dateien, die das Repository ausliefert (`git ls-files`), in einen temporären Ordner. Nicht
   versionierte Dateien in deinem Repository-Ordner, etwa `configs/config.yml`, Schlüssel oder `.env`-Dateien,
   erreichen den Build nie.
3. Docker baut [`Docker Setup/Dockerfile`](../Docker%20Setup/Dockerfile) aus diesem Ordner:
   - Basis: Debian 13 slim, per Digest festgelegt;
   - ein Benutzer `lcf` mit deiner Benutzer- und Gruppen-ID (nie 0, root), damit Dateien in einem eingebundenen
     Datenordner dir gehören; gibt es deine Gruppen-ID in Debian schon (etwa 100, `users`), wird diese Gruppe genutzt;
   - `install.sh` läuft wie auf einem Debian-Computer, als hätte `lcf` es mit `sudo` gestartet: Es installiert Python
     mit `apt`, kopiert das Programm nach `/opt/llm-conversion-framework`, legt dort `.venv` mit der festgelegten
     `requirements.txt` an und schreibt den Starter `llm-framework`. Nur die Docker-Engine und die Gruppe `docker`
     fallen weg (`LCF_CONTAINER_BUILD=1`);
   - `llm-cli` liegt im PATH;
   - die Quellen sind während des Builds nur lesbar eingebunden und werden nie zu einer Image-Schicht; in keiner
     Schicht steht ein Geheimnis;
   - am Ende prüft der Build die Installation: Die Befehlszeile muss sich importieren lassen, und jede
     Systembibliothek, die Qt lädt, muss da sein (mit `--gui` auch die des Desktops und des X11-Plattform-Plugins).
     Sonst bricht der Build ab und nennt, was fehlt, statt ein Image zu liefern, dessen `llm-cli` beim ersten Befehl
     scheitert. Kann `pip` die festgelegten Pakete nicht installieren, bricht `install.sh` den Build ebenfalls ab.
4. Das Image bekommt das lokale Tag `lcf-framework:local`, und der Builder zeigt, wie du es startest.

Ein Build aus dem Repository-Ordner selbst (`docker build -f "Docker Setup/Dockerfile" .`) wird abgelehnt, bevor
etwas kopiert wird, weil dieser Ordner nicht versionierte persönliche Dateien enthalten kann.

| Option | Bedeutung |
|---|---|
| `--gui` | Zusätzlich die Desktop-Bibliotheken für die GUI installieren; Tag `lcf-framework:local-gui` (`make build-gui`) |
| `--tag NAME` | Ein anderes lokales Tag. Ein Name mit Registry (`ghcr.io/...`, `host:5000/...`) wird abgelehnt: Das Image bleibt auf diesem Computer |
| `--uid N`, `--gid N` | Die IDs des Container-Benutzers (Standard: deine; aus 0 wird 1000) |

## 3. Starten und einmal einrichten

```bash
make up                             # docker compose -f "Docker Setup/docker-compose.yml" up -d framework
```

Der Container `lcf-framework` läuft weiter (`sleep infinity`), damit du und deine KI-Clients darin mit `docker exec`
Befehle des Frameworks starten können. Compose lädt das Image nie (`pull_policy: never`), veröffentlicht keinen Port
und entzieht alle Capabilities. Danach einmal:

1. **Wähle, wie der Tresor für Zugangsdaten geschützt wird.** Ein Container hat keinen Schlüsselbund, und das
   Framework wählt nichts für dich: Bis du wählst, brechen Befehle, die das Framework brauchen, mit einer Meldung ab,
   die die Möglichkeiten nennt.
   - **Eine Schlüsseldatei, die der Container als Docker-Secret bekommt** (empfohlen auf einem Server). Lege eine
     Datei an, die nur du lesen kannst, etwa mit `(umask 077 && head -c 32 /dev/urandom | base64 > ~/lcf-vault-key)`
     (die Datei ist nie für andere lesbar, auch nicht für einen Moment), und daneben eine eigene Compose-Datei
     (`~/lcf-compose.override.yml`):

     ```yaml
     services:
       framework:
         secrets: [lcf_vault_key]
       gui:                             # nur, wenn du make gui nutzt
         secrets: [lcf_vault_key]
     secrets:
       lcf_vault_key:
         file: /pfad/zu/lcf-vault-key   # der absolute Pfad deiner Schlüsseldatei
     ```

     Der Container-Benutzer hat deine Benutzer-ID und kann die Datei deshalb lesen. Jeder `make`-Aufruf braucht diese
     Datei ab jetzt: Ohne sie ersetzt `make up` den Container durch einen ohne das Secret, und das Framework bricht ab,
     bis du sie wieder angibst. Setze die Variable deshalb einmal in deinem Shell-Profil (etwa `~/.profile`) und
     öffne ein neues Terminal: `export LCF_COMPOSE_OVERRIDE="$HOME/lcf-compose.override.yml"`. Dann `make up` und
     `docker exec -it lcf-framework llm-cli ditto auth storage --use helper --helper "cat /run/secrets/lcf_vault_key"`.
   - **`--use insecure`**: eine Schlüsseldatei neben dem Tresor im Datenvolume. Wer das Volume lesen kann, kann deine
     Zugangsdaten lesen.
   - `passphrase` fragt bei jedem Start eines Framework-Prozesses und funktioniert deshalb nicht für MCP-Sitzungen,
     die mit `docker exec -i` starten. `LLM_SECRET_STORAGE=ephemeral` hält den Tresor für einen einzelnen Befehl im
     Arbeitsspeicher.
2. **Tool-Berechtigungen:** `docker exec -it lcf-framework llm-cli ditto setup`.
3. **Gesundheitscheck:** `docker exec -it lcf-framework llm-cli doctor` (oder `make doctor`). Er meldet, dass Docker
   nicht erreichbar ist: Der Container baut keine Modelle ([Grenzen](#8-grenzen)).

## 4. KI-Clients: der MCP-Server

Der HTTP-Transport von `llm-cli mcp serve` lauscht nur auf Loopback, und in einem Container ist das der eigene
Loopback des Containers. KI-Clients erreichen das Framework im Container deshalb über **stdio**:

- **Auf demselben Computer:** Der Client startet `docker exec -i lcf-framework llm-cli mcp serve`. Für Claude Code
  führst du selbst `claude mcp add lcf -- docker exec -i lcf-framework llm-cli mcp serve` aus; das Framework startet
  den claude-Befehl nie.
- **Von einem anderen Computer, per SSH zum Host:** Der Befehl des Clients ist
  `ssh benutzer@server docker exec -i lcf-framework llm-cli mcp serve`. Der SSH-Benutzer braucht auf dem Server
  Zugriff auf Docker, und das ist dort so viel wie Root-Zugriff.
- **Ein benanntes Token statt des gemeinsamen Aufrufers `mcp`:** Lege es im Container mit
  `docker exec -it lcf-framework llm-cli mcp token add laptop` an und gib es als Umgebungsvariable weiter, nie auf
  der Befehlszeile: `docker exec -i -e LCF_MCP_TOKEN lcf-framework llm-cli mcp serve`, wobei `LCF_MCP_TOKEN` in der
  Umgebung gesetzt ist, die der Client diesem Befehl mitgibt. Ditto auf einem anderen Computer ergänzt
  `-e LCF_MCP_TOKEN` bei einem `docker exec`-Befehl selbst.
- **Stattdessen HTTP** (fortgeschritten): `llm-cli mcp serve --transport http --container` im Container bindet die
  Schnittstellen des Containers; veröffentliche den Port nur auf dem Loopback des Hosts (`-p 127.0.0.1:8765:8765`)
  und erreiche ihn von einem anderen Computer über einen SSH-Tunnel. Jeder Client braucht sein eigenes benanntes
  Token; die Compose-Datei veröffentlicht keinen Port.

## 5. Wo deine Daten liegen

Alles Persönliche liegt im Docker-Volume `lcf-data`, eingebunden unter `/data`:

| Ordner | Was | Gesetzt durch |
|---|---|---|
| `/data/home` | Persönlicher Zustand: Tool-Berechtigungen, Audit-Log, Tresor für Zugangsdaten, SSH-Schlüssel und bekannte Geräte, MCP-Tokens, Chatverlauf, Wissensbasis | `LLM_FRAMEWORK_HOME` |
| `/data/config` | `config.yml` | `LLM_FRAMEWORK_CONFIG_DIR` |
| `/data/output`, `/data/cache`, `/data/models` | Builds, Cache, heruntergeladene Modelle | `LLM_FRAMEWORK_OUTPUT_DIR`, `..._CACHE_DIR`, `..._MODELS_DIR` |
| `/data/datasets`, `/data/logs` | Kalibrierungsdatensätze, Logdateien | `LLM_FRAMEWORK_DATASETS_DIR`, `..._LOGS_DIR` |
| `/data/huggingface`, `/data/xdg-cache` | Downloads von Hugging Face und andere Caches | `HF_HOME`, `XDG_CACHE_HOME` |

Sichere das Volume wie jedes Docker-Volume.

Der Programmordner `/opt/llm-conversion-framework` gehört `lcf`, so wie er auf dem Rechner selbst deinem Benutzer
gehört. Targets, die du mit dem Modul-Assistenten oder `llm-cli module create` anlegst, und Community-Packs, die du
installierst, landen dort. Sie liegen damit im Container selbst, nicht im Volume, und gehen verloren, wenn der
Container entfernt oder ersetzt wird. Die Make-Ziele entfernen oder ersetzen deshalb nie einen Container, der Dateien
von dir außerhalb des Volumes enthält:

- `make down` stoppt die Container und entfernt keinen.
- `make up` und `make gui` starten einen Container mit Dateien von dir so, wie er ist (ein neues Image oder geänderte
  Einstellungen brauchen dann einen neuen Container, siehe [Aktualisieren](#7-aktualisieren)); ein Container ohne
  solche Dateien wird wie gewohnt ersetzt.
- `make clean` entfernt nur Container, die nichts von dir enthalten. Für jeden anderen listet es jede Datei auf
  (vollständiger Pfad, Dateiname, Größe und was sie ist) und nennt den Befehl, mit dem du den Container selbst
  entfernst; das Volume entfernt es nie.
- `make kept-files` zeigt diese Dateien jederzeit ([`scripts/container_kept_files.sh`](../scripts/container_kept_files.sh)).

## 6. Die GUI (optional)

Das Image ist standardmäßig ohne Oberfläche. Die GUI ist eine ausdrückliche Option:

```bash
make build-gui                      # das Image mit den Desktop-Bibliotheken von Qt
make gui                            # erlaubt deinen Benutzer am X11-Display, startet den Container lcf-framework-gui
```

Der GUI-Container bekommt Zugriff auf dein X11-Display (`/tmp/.X11-unix` und `DISPLAY`) und kann damit Bildschirm
und Tastatureingaben dieses Displays sehen. `make gui` erlaubt nur deinen eigenen Benutzer
(`xhost +SI:localuser:<du>`); der Container-Benutzer hat deine Benutzer-ID, wenn du das Image als du selbst gebaut
hast. Wayland-Desktops funktionieren über XWayland.

## 7. Aktualisieren

```bash
git pull
make build
make up
```

Das Datenvolume bleibt. `make up` ersetzt den Container durch das neue Image, wenn er außerhalb des Volumes nichts von
dir enthält. Sonst lässt es den alten Container weiterlaufen, listet diese Dateien auf und sagt, was zu tun ist:
Kopiere sie zuerst ins Volume (ein eigenes Target als Pack:
`docker exec lcf-framework llm-cli zoo export target NAME --author "<dein Name>" --out /data/packs`, siehe
[CLI_Usage_DE.md](CLI_Usage_DE.md#zoo-export)), entferne den alten Container selbst (`docker rm -f lcf-framework`),
führe `make up` erneut aus und installiere die Packs wieder.

## 8. Grenzen

- **Keine Konvertierungen im Container.** Das Image enthält keine Docker-Engine, und Builds brauchen Docker auf dem
  Computer, der konvertiert. Baue auf einem Computer, auf dem das Framework direkt installiert ist. Den Docker-Socket
  des Hosts in den Container zu geben wird nicht angeboten: Der Container bekäme damit Rechte wie Root auf dem Host.
- **Kein Schlüsselbund:** siehe Schritt 1 unter [Starten und einmal einrichten](#3-starten-und-einmal-einrichten).
- **Targets und Community-Packs** liegen im Programmordner des Containers, nicht im Volume: Der Wechsel zu einem
  neuen Image braucht vorher einen Export ([Aktualisieren](#7-aktualisieren)).
- **Gesprochene Ansagen** sind aus (`LLM_FRAMEWORK_NO_AUDIO=1`).
- Das Image wird in der CI gebaut und mit Rauchtests geprüft (`.github/workflows/framework-container.yml`); auf
  Proxmox oder einem NAS ist es noch nicht getestet.

## 9. Die Dateien

| Datei | Zweck |
|---|---|
| [`scripts/build_container_image.sh`](../scripts/build_container_image.sh) | Der Builder: prüft, stellt die ausgelieferten Dateien bereit, baut mit lokalem Tag, zeigt, wie du es startest |
| [`scripts/container_kept_files.sh`](../scripts/container_kept_files.sh) | Listet die Dateien von dir auf, die ein Container außerhalb des Datenvolumes enthält; die Make-Ziele entfernen oder ersetzen einen solchen Container nie |
| [`Docker Setup/Dockerfile`](../Docker%20Setup/Dockerfile) | Das Image: Debian 13 slim per Digest, `install.sh`, Benutzer `lcf`, `/data`, standardmäßig ohne Oberfläche |
| [`Docker Setup/Dockerfile.dockerignore`](../Docker%20Setup/Dockerfile.dockerignore) | Hält große und persönliche Ordner aus einem Build-Kontext heraus, der aus dem Repository-Ordner selbst entsteht |
| [`Docker Setup/docker-compose.yml`](../Docker%20Setup/docker-compose.yml) | Die Dienste `framework` und `gui` (Profil `gui`), das Volume `lcf-data`, `pull_policy: never` |
| [`Makefile`](../Makefile) | `make build`, `build-gui`, `up`, `gui`, `down`, `kept-files`, `doctor`, `shell`, `clean`, `audit` (Trivy prüft ein Archiv des Images; der Scanner bekommt nie den Docker-Socket) |
| `Docker Setup/pyproject.toml` | Eine alte Kopie der Projektdatei (Version 1.6.0), die nichts nutzt; die Projektdatei ist [`pyproject.toml`](../pyproject.toml) oben im Repository |
