Version 3.0.0 plus die [unveröffentlichten Änderungen](CHANGELOG_DE.md#unreleased) · Zuletzt aktualisiert am 2026-10-11

[English](README.md) · **Deutsch**

# LLM Cross-Compiler Framework

Das Framework bereitet Sprachmodelle (LLMs) so auf, dass sie auf kleinen Edge-Geräten laufen, zunächst auf
Rockchip-Boards wie dem Radxa ZERO 3W oder dem Radxa ROCK 5B. Es besteht aus einer Desktop-App (GUI), einer
Kommandozeile (`llm-cli`), einem Server, über den KI-Clients wie Claude Code, Claude Desktop oder VS Code das
Framework bedienen (MCP), und dem eingebauten Assistenten **Ditto**.

Diese README beschreibt den Stand des Entwicklungszweigs: Release 3.0.0 (2026-10-10) plus die Arbeiten, die im
[CHANGELOG](CHANGELOG_DE.md) unter „Unreleased“ stehen. Was als Nächstes kommt, steht in der
[Roadmap](docs/BLUEPRINT_DE.md#roadmap).

---

## Was macht das Framework?

Sprachmodelle werden meist für große PCs veröffentlicht: mehrere Gigabyte groß und in Formaten, mit denen ein
kleines Board nicht effizient umgehen kann. Das Framework nimmt so ein Modell und bereitet es für ein bestimmtes
Board auf:

1. **Holen:** ein Modell von [Hugging Face](https://huggingface.co/) (zum Beispiel
   `HuggingFaceTB/SmolLM2-135M-Instruct`) oder einen Ordner bzw. eine Datei von deinem Rechner.
2. **Konvertieren:** in ein Format, das das Board versteht: **GGUF** für den Prozessor (CPU, über llama.cpp),
   **RKLLM** für den KI-Beschleuniger (NPU) der Rockchip-Chips, **RKNN** für Bild- und Audiomodelle auf der NPU.
3. **Quantisieren:** die Zahlen des Modells mit weniger Bits speichern (zum Beispiel 4 statt 16). Das Modell wird
   deutlich kleiner und schneller und verliert dabei etwas Qualität.
4. **Übertragen:** das Ergebnis per SSH auf das Board kopieren und mit Prüfsummen kontrollieren.

Die Konvertierungswerkzeuge laufen in einem Docker-Container auf deinem PC; Hersteller-SDKs musst du nicht selbst
installieren. Während der Konvertierung hat der Container keinen Netzwerkzugang. Jeder Build schreibt ein Manifest
(`build_info.json`) mit den Modelldateien und ihren SHA256-Hashes sowie eine `Model_Card.md`.

Für GGUF-Modelle kann das Framework llama.cpp auch für das Board bauen und dort installieren; die Laufzeiten für
RKLLM und RKNN installierst du selbst (siehe [Das Board](#das-board)).

## Unterstützte Targets und Formate

| Target | Stand | Boards | Formate |
|---|---|---|---|
| Rockchip | offiziell | RK3566, RK3568, RK3576, RK3588 | GGUF, RKLLM, RKNN |
| NVIDIA-GPUs, AMD (Ryzen Z1 Extreme) | geplant ([Roadmap](docs/BLUEPRINT_DE.md#roadmap)) | – | – |
| Allgemeine ARM64-CPU (Raspberry Pi 4 und 5) | geplant ([Roadmap](docs/BLUEPRINT_DE.md#roadmap)) | – | GGUF |

Andere Hardware kannst du selbst ergänzen: [docs/Module_Developer_Guide_DE.md](docs/Module_Developer_Guide_DE.md).

Die Werte unten stammen aus dem Block `capabilities:` in [`targets/Rockchip/target.yml`](targets/Rockchip/target.yml).
GUI, CLI, Ditto und der MCP-Server bieten genau diese Optionen an und lehnen alles andere ab.

| Board (`--board`) | CPU | NPU | RAM (max.) | Beispiel-Boards |
|---|---|---|---|---|
| `rk3566` | 4× Cortex-A55 | 1 TOPS | 8 GB | Radxa ZERO 3W, Radxa CM3, ROCK 3C |
| `rk3568` | 4× Cortex-A55 | 1 TOPS | 8 GB | ROCK 3A, Radxa E25 |
| `rk3576` | 4× Cortex-A72 + 4× Cortex-A53 | 6 TOPS | 16 GB | Radxa ROCK 4D |
| `rk3588` | 4× Cortex-A76 + 4× Cortex-A55 | 6 TOPS | 32 GB | Radxa ROCK 5B, Radxa ROCK 5ITX, Orange Pi 5 |

| Format (`--format`) | Läuft auf | Eingabe | Boards | Quantisierungen | Standard |
|---|---|---|---|---|---|
| `gguf` | CPU (llama.cpp v0.6.0) | Hugging-Face-Modell | alle vier | 33 llama.cpp-Typen, z. B. `Q4_K_M`, `Q4_0`, `IQ4_NL`, `IQ4_XS`, `Q5_K_M`, `Q6_K`, `Q8_0`, `F16` | `Q4_K_M`; `Q4_0` auf rk3566/rk3568 |
| `rkllm` | NPU, Sprachmodelle (RKLLM-Toolkit 1.3.1) | Hugging-Face-Modell | rk3588, rk3576 | rk3588: `w8a8`, `w8a8_g128`, `w8a8_g256`, `w8a8_g512` · rk3576: `w4a16`, `w4a16_g32`, `w4a16_g64`, `w4a16_g128`, `w8a8` | `w8a8` (rk3588), `w4a16` (rk3576) |
| `rknn` | NPU, ONNX-Bild-/Audiomodelle (RKNN-Toolkit2 2.3.2) | lokale `.onnx`-Datei | alle vier | `fp16`, `i8` | `fp16` |

<details>
<summary>Alle GGUF-Quantisierungstypen</summary>

`Q4_K_M`, `Q4_0`, `IQ4_NL`, `IQ4_XS`, `Q5_K_M`, `Q6_K`, `Q8_0`, `Q4_1`, `Q5_0`, `Q5_1`, `Q2_K`, `Q2_K_S`,
`Q3_K_S`, `Q3_K_M`, `Q3_K_L`, `Q4_K_S`, `Q5_K_S`, `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`, `IQ2_M`,
`IQ3_XXS`, `IQ3_XS`, `IQ3_S`, `IQ3_M`, `TQ1_0`, `TQ2_0`, `MXFP4_MOE`, `F16`, `BF16`, `F32`
</details>

- **RK3566 und RK3568** werden von RKLLM nicht unterstützt; dort nimmst du GGUF. `Q4_0` und `IQ4_NL` werden für
  schnelle ARM-Kernel umgepackt.
- **Importance Matrix (IMatrix):** nur für GGUF. Sie verbessert Quantisierungen mit wenigen Bits. `IQ1_S`,
  `IQ1_M`, `IQ2_XXS`, `IQ2_XS` und `IQ2_S` setzen sie voraus. Ohne eigene Textdatei nimmt das Framework seinen
  mitgelieferten Kalibrierungstext ([data/calibration](data/calibration/README_DE.md)).
- **Kalibrierungsdaten:** für RKLLM und RKNN optional. RKNN `i8` braucht eine Liste mit Beispieleingaben (Pfade
  relativ zur Listendatei).
- Gängige Schreibweisen werden angenommen und übersetzt, zum Beispiel `INT8` → `Q8_0` für GGUF oder `int4` →
  `w4a16` für RKLLM. Bei Board-Namen spielt Groß- und Kleinschreibung keine Rolle.

---

## Voraussetzungen

**Dein PC (der „Host“)**

- Ein **x86-64-Rechner** (Intel oder AMD). Das Rockchip-Build-Image installiert die Toolkits von Rockchip als
  Pakete für x86-64-Linux.
- **Linux** (der Installer nutzt `apt`, also Debian, Ubuntu und Verwandte) oder **Windows** mit Docker Desktop.
- **Python 3.11 bis 3.14.**
- **Docker:** unter Linux Docker Engine (der Installer richtet es ein, falls es fehlt); unter Windows **Docker
  Desktop** mit **WSL-2-Backend**, installiert und gestartet, bevor du das Framework installierst.
- **Speicherplatz:** mindestens 20 GB frei für das Build-Image, heruntergeladene Modelle und Ergebnisse
  (`llm-cli mojo` warnt unter 20 GB und meldet unter 5 GB einen Fehler). Große Modelle brauchen während der
  Konvertierung das Zwei- bis Dreifache ihrer Größe.
- **Internetzugang** für die Installation, den ersten Image-Build und Modell-Downloads.

**Das Board (nur fürs Übertragen)**

- Linux mit SSH-Server, ein Benutzerkonto, mit dem du dich anmelden kannst, und die Programme `unzip` und
  `sha256sum`.

---

## Installation

### Linux

Öffne ein Terminal im heruntergeladenen Repository-Ordner und starte:

```bash
sudo ./install.sh
```

Der Installer:

1. prüft die Internetverbindung (HTTPS zum Paketindex `pypi.org` oder zu `PIP_INDEX_URL`, wenn du einen Spiegel
   setzt; hinter einem Proxy startest du ihn mit `sudo -E`, damit `https_proxy` erhalten bleibt) und Python
   (installiert es mit `apt`, falls es fehlt, und verlangt dann 3.11 bis 3.14),
2. installiert Docker über `get.docker.com`, falls es fehlt, und nimmt deinen Benutzer in die Gruppe `docker` auf.
   Er sagt, was das heißt: Wer in der Gruppe ist, steuert den Docker-Dienst, und der läuft als root; jedes
   Programm, das als dein Benutzer läuft, kann damit Root-Rechte auf diesem Rechner bekommen
   (`sudo gpasswd -d BENUTZER docker` macht es rückgängig; danach kann das Framework ohne sudo nicht bauen),
3. kopiert die Dateien, die das Repository ausliefert, nach `/opt/llm-conversion-framework` (Eigentümer ist dein
   Benutzer) und führt sie in `.lcf-installed-files` auf. Ein erneuter Aufruf aktualisiert diese Dateien und
   entfernt nur Dateien, die ein früherer Aufruf kopiert hat und die das Repository nicht mehr ausliefert; alles
   andere dort bleibt: deine eigenen Targets (Modul-Assistent, `llm-cli module create`), installierte
   Community-Packs, `configs/config.yml`, Builds, Cache und Modelle. Starte Updates im Repository-Ordner: Die
   Kopie von `install.sh` in `/opt` kopiert und entfernt nichts,
4. legt dort eine virtuelle Umgebung (`.venv`) an und installiert die festgelegten Pakete aus `requirements.txt`,
5. legt den Starter `/usr/local/bin/llm-framework` an, der die GUI startet,
6. richtet Dittos lokalen Modellserver ein (`llm-cli ditto runtime ensure`, als du, nie als root): standardmäßig
   direkt, in Docker, wenn du das wählst oder wenn es direkt hier nicht geht. Ist Ditto schon da, tut der Schritt
   nichts. Scheitert er, sagt der Installer warum und wird trotzdem fertig. Bevor ein Modell heruntergeladen wird,
   siehst du seine Größe und Lizenz. Siehe [Offline-Modus](#offline-modus-das-verwaltete-lokale-modell).

Den Ditto-Schritt wählst du mit `sudo ./install.sh --ditto=auto|bare-metal|docker|skip` (Standard `auto`). Für einen
unbeaufsichtigten Lauf setzt du stattdessen `LCF_DITTO` (zum Beispiel `sudo LCF_DITTO=auto ./install.sh`): Dann wird
nichts gefragt, und ein Modell unter einer freien Lizenz (etwa Apache 2.0) wird heruntergeladen, nachdem Größe und
Lizenz angezeigt wurden.

Melde dich einmal ab und wieder an, damit die Gruppe `docker` wirkt. Danach startest du die GUI mit
`llm-framework`.

### Windows

1. Docker Desktop (WSL-2-Backend) installieren und starten.
2. Rechtsklick auf `install.bat` und **Als Administrator ausführen** wählen.

Der Installer sucht Python (installiert Python 3.13 mit `winget`, wenn keines gefunden wird, und verlangt dann
3.11 bis 3.14), kopiert das Framework nach `C:\Program Files\LLM-Conversion-Framework`, legt dort `.venv` an,
installiert `requirements.txt` und legt eine Verknüpfung **LLM-Conversion-Framework** auf den Desktop. Die
Verknüpfung startet `start_framework.bat` und damit die GUI. Der Ditto-Schritt ist derselbe wie unter Linux
(`install.bat --ditto=...` oder `LCF_DITTO`); er läuft nur für die angemeldete Person. In dieser Version hat Windows
noch keinen selbst gebauten Server und keinen Docker-Modus für Ditto, also sagt der Schritt das, und die
Installation geht weiter.

Konvertierungen auf einem Windows-Host sind noch nicht geprüft (siehe [Bekannte Lücken](#bekannte-lücken)).

### In einem Container (Server, NAS, Proxmox)

Kein Framework-Image wird veröffentlicht oder geladen: Du baust das Image auf deinem eigenen Computer, und es führt im
Container dieselbe Installation aus wie oben (`install.sh`), aus genau den Dateien, die das Repository ausliefert. Es
ist ohne Oberfläche (CLI und MCP-Server); die GUI ist eine ausdrückliche Option.

```bash
make build      # scripts/build_container_image.sh: prüft Docker, baut lcf-framework:local hier, zeigt, wie du es startest
make up         # startet den Container lcf-framework; deine Daten liegen im Volume lcf-data
docker exec -it lcf-framework llm-cli mojo
```

KI-Clients erreichen seinen MCP-Server über stdio (`docker exec -i lcf-framework llm-cli mcp serve`, von einem
anderen Computer per SSH zum Host). Der Container baut selbst keine Modelle (er enthält kein Docker). Einrichtung,
Tresor für Zugangsdaten, Daten, GUI und Updates: [docs/Container_Setup_DE.md](docs/Container_Setup_DE.md).

### `llm-cli` aufrufen

Die Installer legen `llm-cli` nicht in deinen PATH. Nimm das Python des Frameworks im Installationsordner:

```bash
# Linux
cd /opt/llm-conversion-framework
.venv/bin/python -m orchestrator.cli --help
```

```bat
:: Windows (Eingabeaufforderung)
cd /d "C:\Program Files\LLM-Conversion-Framework"
.venv\Scripts\python.exe -m orchestrator.cli --help
```

Unter Linux kannst du dir eine Abkürzung anlegen, die aus jedem Ordner funktioniert:

```bash
alias llm-cli='PYTHONPATH=/opt/llm-conversion-framework /opt/llm-conversion-framework/.venv/bin/python -m orchestrator.cli'
```

In einer Entwicklungsumgebung (siehe [CONTRIBUTING_DE.md](CONTRIBUTING_DE.md)) gibt es nach `uv sync` den
Befehl `llm-cli`. Die Beispiele in dieser README verwenden `llm-cli`.

### Wo deine Dateien liegen

| Was | Wo |
|---|---|
| Programm und Python-Umgebung | Linux: `/opt/llm-conversion-framework` · Windows: `C:\Program Files\LLM-Conversion-Framework` |
| Fertige Modelle | Der Ausgabeordner `~/llmcf/output` (Windows: `%USERPROFILE%\llmcf\output`), einmal festgelegt mit **File > Output Folder...** oder `llm-cli config output-folder`; in der GUI öffnet sich dort für jeden fertigen Build ein Speichern-Dialog (unveröffentlicht) |
| Noch nicht gespeicherte Builds, Cache, heruntergeladene Modelle | `output/` (der Build-Ordner des Frameworks; frühere Builds bleiben dort und werden weiter aufgelistet), `cache/` und `models/` im Installationsordner, wenn dein Benutzer dort schreiben darf (Linux), sonst im persönlichen Ordner unten (Windows: `%USERPROFILE%\.llm-framework\output` und so weiter); sie entstehen, wenn sie zum ersten Mal gebraucht werden. Die Einstellungen `build_dir`, `cache_dir` und `models_dir` legen andere Ordner fest |
| Einstellungen (`config.yml`) | `configs/` im Installationsordner, wenn dein Benutzer dort schreiben darf, sonst der persönliche Ordner unten; `LLM_FRAMEWORK_CONFIG_DIR` legt einen anderen Ordner fest |
| Persönlicher Zustand: Tool-Berechtigungen, Audit-Log, SSH-Schlüssel und bekannte Geräte, MCP-Token, Chatverlauf, Eingabeverlauf, Wissensbasis, lokaler Modellserver, zwischengespeicherter Community-Index | `~/.llm-framework` (Windows: `%USERPROFILE%\.llm-framework`); `LLM_FRAMEWORK_HOME` legt einen anderen Ordner fest |
| API-Schlüssel und Tokens | verschlüsselt in `~/.llm-framework/secrets.store`; der Schlüssel dafür liegt im Schlüsselbund deines Betriebssystems oder dort, wo du es mit der Einstellung `secret_storage` festlegst |
| Modelle für Dittos Offline-Modus | `ditto/` im Modellordner oben (`LLM_FRAMEWORK_MODELS_DIR` legt einen anderen Ordner fest) |

Die Installer legen keine eigenen Datenordner an.

---

## Schnellstart: die GUI

1. **Starten:** `llm-framework` unter Linux, die Desktop-Verknüpfung unter Windows.
2. **Sprache:** Beim ersten Start fragt ein Dialog nach Englisch oder Deutsch. Die Wahl wird als `language`
   gespeichert.
3. **Tool-Berechtigungen („Ditto-Einrichtung“):** Lege einmal fest, was Ditto und verbundene KI-Clients selbst
   tun dürfen:

   | Kategorie | Empfohlen | Auswahl |
   |---|---|---|
   | Informationen lesen (Targets, Builds, Logs, Wissensbasis) | Erlauben | Erlauben, Fragen, Verbieten |
   | Auf diesem Rechner etwas ändern (Builds starten oder abbrechen) | Fragen | Erlauben, Fragen, Verbieten |
   | Andere Geräte erreichen oder Daten löschen | Fragen | Fragen, Verbieten (nie „Erlauben“ für die ganze Kategorie) |

   Darunter kannst du einzelne Tools abweichend einstellen. Später änderst du das unter **Tools > Tool
   Permissions (Ditto / MCP)...** oder mit `llm-cli tools permissions`.
4. **Gesundheitscheck (empfohlen):** `llm-cli mojo` prüft Python, Docker, Speicherplatz, Targets,
   Sicherheitseinstellungen, Tool-Berechtigungen und die Kalibrierungsdaten und erklärt, wie du Probleme behebst.

**Reiter „Builder“:** Hier konvertierst du ein Modell.

1. **Source Model:** eine Hugging-Face-ID oder ein lokaler Ordner bzw. eine Datei über die Ordner-Schaltfläche.
2. **Target Hardware:** das Target (Profil). **Scan / Import...** liest die Hardware eines Boards ein: eine
   Probe-Datei importieren, ein Board per SSH scannen oder diesen PC scannen.
3. **AI Optimization (Ditto):** optional. Setze den Haken bei **Enable Ditto AI Optimization**; dann schlägt
   **Ask Ditto for Config** Format, Quantisierung und Optimierung aus den Optionen des Targets vor. Dafür braucht es
   einen eingerichteten KI-Anbieter.
4. **Build Configuration:** Format, Board, Quantisierung und Optimierung. Angeboten werden nur Kombinationen, die
   das Target unterstützt. **Calibration:** Importance Matrix (GGUF) und eine Kalibrierungsdatei. **Use Host GPU
   for Build Process** reicht eine GPU in den Container durch; Rockchip-Konvertierungen brauchen das nicht.
5. **Start Build Pipeline:** Das Log erscheint rechts. Der erste Build dauert deutlich länger, weil zuerst das
   Docker-Image mit allen Toolkits gebaut wird; spätere Builds nutzen Dockers Build-Cache. **Cancel Build** stoppt
   den laufenden Build nach einer Bestätigung; in den Ausgabeordner wird dann nichts geschrieben.

Scheitert ein Build an einem bekannten Problem, zeigt ein **Self-Healing**-Dialog die Ursache und einen
Lösungsvorschlag. Nichts ändert sich, bevor du zustimmst. Selbst ausführen kann das Framework nur wenige feste
Schritte (weniger parallele Compile-Jobs nach Speichermangel, eigene Images und den Build-Cache löschen, wenn die
Platte voll ist, ein erneuter Versuch nach einem Netzwerkfehler). Vorschläge einer KI werden nur als Rat angezeigt.

**KI-Chat-Bereich rechts:** mit Ditto sprechen (siehe [Ditto](#ditto-der-eingebaute-assistent)); auch Fragen, die
auf dich warten, und was KI-Apps für dich getan haben, kommen dort an. F9 oder **Ansicht > KI-Chat-Bereich** zeigt
oder verbirgt ihn. **Reiter „Deployment“:** einen fertigen Build auf ein Board kopieren. Die Reiter **Auto-Tuning**
und **Models** sind Platzhalter.

Menüs: **Security > Set OpenAI API Key**, **Tools > Configure AI Agent (Ditto)...**, **Tools > Create Hardware
Module...** (Assistent für ein neues Target, mit „Deep Ingest“, um Dokumentation in die Wissensbasis aufzunehmen),
**Tools > Hugging Face Model Browser...** (Modelle suchen, sehen, ob sie zugangsbeschränkt sind und welche Lizenz sie
haben, einzelne Dateien wie ein GGUF in den Modellordner laden; ein zugangsbeschränktes Modell fragt nach deinem
Hugging-Face-Token), **Tools > Community Hub öffnen** (siehe [Community-Zoo](#community-zoo)), **Tools > Tool Permissions (Ditto /
MCP)...**, **Tools > Gesprochene Ansagen** (ein oder aus) und **Tools > Eingabeverlauf löschen...** (in der
englischen Oberfläche *Open Community Hub*, *Spoken Announcements* und *Clear Input History...*)

Die Eingabefelder bieten an, was du zuletzt verwendet hast (Modelle, Targets, Boards, Datensätze, URLs, Geräte), pro
Benutzer und nie Geheimnisse: [Einzelheiten](docs/Features_DE.md#eingabeverlauf).

### Auf das Board übertragen

1. Wähle im Reiter **Deployment** einen Build. Builds werden aus ihren Manifesten aufgelistet.
2. Wähle das Profil (das Target, z. B. `Rockchip`), gib IP-Adresse und Benutzer des Boards ein und klicke auf
   **Deploy to Target**.
3. **Erste Verbindung:** Das Framework zeigt den Fingerabdruck des SSH-Host-Schlüssels des Boards. Vergleiche ihn
   mit der Ausgabe von `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` auf dem Board und bestätige. Danach gibst
   du einmal das Passwort ein: Damit wird der eigene SSH-Schlüssel des Frameworks auf dem Board hinterlegt; das
   Passwort wird nicht gespeichert. Spätere Übertragungen brauchen kein Passwort.
4. Das Framework packt Modell, Manifest, Model Card, Prüfsummen und ein `deploy.sh` in eine ZIP-Datei in
   `output/`, lädt sie in einen privaten temporären Ordner auf dem Board und führt dort `deploy.sh` aus. Das Skript
   prüft die Prüfsummen, kopiert das Modell nach `~/models` (einen anderen Ordner legst du mit `MODEL_DIR` fest)
   und löscht danach den temporären Ordner.

Lass die Docker-Option für eine einfache Modelldatei aus. Mit ihr enthält das Paket zusätzlich Container-Images;
dieser Modus ist experimentell und in dieser Version nicht geprüft, und er nimmt ein Image nur, wenn es per Digest
festgelegt ist (`name@sha256:...`) oder auf deinem Rechner gebaut wurde.

Nach einem erfolgreichen Deployment gehört das Board zu deinen **vertrauten Geräten** (`llm-cli deploy devices
list`). Ein über MCP verbundener KI-Client oder Ditto kann dann einen Build paketieren und auf dieses Board übertragen
(`list_devices`, `create_deploy_package`, `deploy_to_device`), jedes Mal erst, nachdem du eine Frage bestätigt hast,
die das Board, den Fingerabdruck seines Host-Schlüssels und den Hash des Pakets zeigt. Keine KI kann einem neuen Board
vertrauen oder einen geänderten Host-Schlüssel annehmen.

### Das Board

Um das Modell auf dem Board auszuführen, brauchst du dort eine Laufzeitumgebung: llama.cpp (`llama-server`) für
GGUF, Rockchips RKLLM-Runtime für `.rkllm`-Dateien (RK3588/RK3576) und die RKNN-Runtime für `.rknn`-Dateien.
llama.cpp baut und installiert das Framework selbst für das Board (unten); die RKLLM- und RKNN-Runtime installierst
du selbst. Für GGUF gibt `deploy.sh` aus, wie du das Modell mit dem Startskript des Frameworks startest, wenn die
Laufzeit installiert ist, sonst, wie du sie deployst.

**Die llama.cpp-Laufzeit auf ein Board bringen** (Kommandozeile; Docker auf deinem PC für den Build):

```bash
llm-cli scan --device 192.168.1.50 --user dietpi   # das Board untersuchen (einmal, und nach einem OS-Upgrade erneut)
llm-cli device settings 192.168.1.50                # wie die Laufzeit startet (Vorgabe: das Startskript)
llm-cli device runtime build 192.168.1.50           # für dieses Board im Builder-Container cross-kompilieren
llm-cli device runtime deploy 192.168.1.50          # per SSH installieren und auf dem Board prüfen
```

- **Für das Board gebaut:** Der Bauplan kommt aus der eigenen Probe des Boards: seine Architektur und
  CPU-Merkmale (beim RK3566 `armv8.2-a+dotprod+fp16`) und seine C-Bibliothek mit Version. Der Builder-Container
  nimmt eine Basis, deren glibc (oder musl unter Alpine) nicht neuer ist als die des Boards, linkt die C++-Laufzeit
  statisch und hat beim Bauen kein Netz. `llm-cli device runtime plan` zeigt den Bauplan vorher.
- **In deinem Home installiert:** unter `~/.local/share/llm-framework/llama.cpp/` auf dem Board, nie als root und
  nie mit Dateien in `/tmp`. Der vorige Build bleibt neben dem neuen liegen.
- **Auf Loopback mit Schlüssel gestartet:** `~/.local/share/llm-framework/llama.cpp/current/llama-server.sh start
  ~/models/model.gguf` startet `llama-server` auf 127.0.0.1:8080 mit dem Schlüssel aus
  `~/.config/llm-framework/llama-server.key` (auf dem Board erzeugt, nur für dich lesbar) und einer geleerten
  Umgebung; `stop`, `status` und `serve` (im Vordergrund) funktionieren genauso. Optionen wie `-c 4096` oder `-t 4`
  werden weitergereicht; Optionen, die dem Modell Werkzeuge geben, die Weboberfläche öffnen oder Loopback verlassen
  würden, werden abgelehnt.
- **Startskript oder systemd-Unit, vorher entschieden:** `llm-cli device settings GERÄT --install systemd --model
  models/model.gguf` wählt eine systemd-Unit, die den Server beim Booten startet. Sie braucht systemd auf dem Board
  und deine Bestätigung, gleich an dieser Stelle, dass Deployments dort `sudo -n` ausführen dürfen; ein Deployment
  selbst fragt nie. Boards ohne systemd (OpenRC unter Alpine, SysV-Init) nutzen das Startskript.
- **Auf dem Board geprüft, bevor er genutzt wird:** Die Programme des neuen Builds laufen als dein Benutzer: Die
  Version muss die festgelegte sein, die Geräte werden aufgelistet, und der Server muss `--tools` ablehnen.
  `--bench-model models/model.gguf` ergänzt einen kurzen `llama-bench`-Lauf; ein Modell, das keines der
  Katalogmodelle des Frameworks ist, läuft nur nach deiner Zustimmung, die ins Audit-Log geschrieben wird. Nur ein
  Build, der besteht, wird der aktive (und der der systemd-Unit); ein Build, der scheitert, wird wieder entfernt, und
  der vorige Build bleibt aktiv.

Noch nicht auf einem echten Board getestet; die GUI dafür folgt. KI-Clients erreichen dieselben Schritte über die optionalen MCP-Tools `probe_remote_device`, `deploy_device_runtime` und `run_device_benchmark`, die verborgen bleiben, bis du sie freigibst, und bei jedem Aufruf von dir bestätigt werden.

### Die Hardware des Boards untersuchen

Ein Build wird für die Hardware geplant, auf der er laufen wird, deshalb läuft die Hardware-Probe auf diesem Rechner:

```bash
llm-cli scan --device 192.168.1.50 --user rock   # ein Board per SSH (Python-Probe mit Python 3.8+, sonst Shell-Probe)
llm-cli scan                                      # dieser Rechner
```

Jeder Scan schreibt `target_hardware_config.txt` und behält das Profil in `~/.llm-framework/hardware/`. Das Profil
enthält das Betriebssystem (ID, Version, Familie wie Debian, Variante wie DietPi, Kernel, Init-System), die
Architektur, die C-Bibliothek und ihre Version (glibc oder musl), die CPU mit ihren Merkmalen und Kernclustern,
Speicher, SoC, NPU und GPUs; keinen Hostnamen, keine Seriennummer und keine MAC-Adresse. Details:
[CLI_Usage_DE.md](docs/CLI_Usage_DE.md#scan).

**Rechner ohne Netzzugang (air-gapped):** Kopiere eine Probe aus `scripts/` auf den Rechner, etwa per USB-Stick, führe
sie dort aus und bring `target_hardware_config.txt` zurück:

| Auf dem Rechner | Befehl |
|---|---|
| Python 3.8 oder neuer (Linux, Windows, macOS) | `python3 -I hardware_probe.py` |
| Linux ohne Python (jede POSIX-Shell, auch BusyBox) | `sh hardware_probe.sh` |
| Windows ohne Python | `powershell -ExecutionPolicy Bypass -File hardware_probe.ps1` |

Die Probes brauchen kein Netz, fragen nichts und installieren nichts. Die interaktiven Funktionen der alten Skripte
bleiben als Optionen erhalten: `hardware_probe.sh --install-tools` bietet zuerst an, `vulkan-tools` mit dem
Paketmanager zu installieren, und `hardware_probe.ps1 -Elevate` startet sich mit Administratorrechten neu. Die Datei
holst du in der GUI mit **Scan / Import... > Import from File (USB/Disk)...** ins Framework oder übergibst sie an
`llm-cli module generate-ai`.

---

## Schnellstart: die Kommandozeile

```bash
llm-cli --version
llm-cli mojo                   # Gesundheitscheck (Mojo Oracle); Exit-Code 0 = OK, 1 = Warnungen, 2 = Fehler; im Terminal bietet er Mojo Miracle an
llm-cli mojo --miracle         # reparieren, was harmlos ist (macht den persönlichen Ordner privat), dann sagen, was repariert wurde, was nicht und warum
llm-cli targets list           # verfügbare Targets mit Boards und Formaten; der Wert für --target ist die ID (Rockchip)
```

**Ein Modell konvertieren** (`--target Rockchip`; Format, Board und Quantisierung wie in den Tabellen oben):

```bash
# GGUF für die CPU des RK3566 (Radxa ZERO 3W), Standard-Quantisierung Q4_0, Fortschritt verfolgen
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip --format gguf --board rk3566 --follow

# RKLLM für die NPU des RK3588
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3588 -q w8a8 -F

# GGUF mit Importance Matrix aus deinem eigenen Kalibrierungstext
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3588 -q IQ4_XS --imatrix --dataset ./calibration.txt -F

# RKNN aus einer lokalen ONNX-Datei
llm-cli build start -m ./my_model.onnx -t Rockchip -f rknn -b rk3566 -q fp16 -F
```

- Ergebnisse landen im Ausgabeordner, `~/llmcf/output/Rockchip/<Modell>/<Quantisierung>/` (Modelldatei,
  `build_info.json`, `Model_Card.md` und das ZIP daneben). Im Terminal wirst du vorher gefragt, mit dem Ausgabeordner
  als Vorgabe; `-o/--output` legt einen anderen Ordner fest (unveröffentlicht; vorher `output/build_<Datum>_<Uhrzeit>/`
  in der Installation).
- Eine Kombination, die das Target nicht unterstützt, stoppt, bevor etwas läuft, mit Exit-Code 2 und dem Grund
  (zum Beispiel „Format 'rkllm' does not run on rk3566“).
- Zugangsbeschränkte Hugging-Face-Modelle brauchen ein Token: `llm-cli secrets set hf_token` (fragt nach dem
  Wert).
- Der Befehl bleibt im Vordergrund, bis der Build fertig ist; `--follow` zeigt jede Stufe. Wartet der Build auf
  eine Self-Healing-Entscheidung, fragt die CLI im Terminal. Strg+C bricht den Build ab (sein Container wird
  gestoppt), wartet, bis er angehalten hat, und endet mit Exit-Code 130. `llm-cli build status` listet auch Builds,
  die die GUI, der MCP-Server oder ein anderer CLI-Aufruf gestartet hat.

**Übertragen** (dieselben Schritte wie in der GUI):

```bash
llm-cli deploy list-artifacts
llm-cli deploy package build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
llm-cli deploy run output/deploy_<modell>_<zeitstempel>.zip --host 192.168.1.50 --user rock
```

Alle Befehle und Optionen mit Beispielen: [docs/CLI_Usage_DE.md](docs/CLI_Usage_DE.md).

---

## Schnellstart: KI-Clients über MCP (Claude Code, Claude Desktop, VS Code)

Das Framework läuft als [Model-Context-Protocol](https://modelcontextprotocol.io)-Server. Ein KI-Client, den du
schon nutzt, kann es dann bedienen: Targets auflisten, prüfen, was ein Board kann, Builds starten und beobachten,
Logs lesen, die Wissensbasis durchsuchen, den Gesundheitscheck ausführen und über Self-Healing-Vorschläge
entscheiden. Am Client meldest du dich mit deinem eigenen Konto oder Schlüssel an; das Framework sieht diese
Zugangsdaten nie.

```bash
llm-cli ditto setup                         # einmalig: was KI-Clients selbst dürfen, was deine Bestätigung braucht
llm-cli mcp config                          # zeigt die Einrichtung für Claude Code, Claude Desktop und VS Code
llm-cli mcp config --write claude-desktop   # oder direkt in Claude Desktop eintragen (andere Einträge bleiben, eine Sicherung wird geschrieben)
```

`llm-cli mcp config` gibt für Claude Code einen Befehl `claude mcp add ...` und für Claude Desktop und VS Code
(`.vscode/mcp.json`) JSON-Schnipsel aus, jeweils mit dem Python und dem Ordner deiner Installation. Ruf es mit dem
Python des Frameworks auf (siehe [`llm-cli` aufrufen](#llm-cli-aufrufen)). Nach `--write claude-desktop` startest
du Claude Desktop neu.

- **Berechtigungen:** Nur lesende Tools sind standardmäßig erlaubt. Builds starten oder abbrechen und über
  Self-Healing-Vorschläge entscheiden fragt nach deiner Bestätigung, die der Client als Frage anzeigt, außer du
  hast es erlaubt. Kann ein Client dich nicht fragen (zum Beispiel Claude-Desktop-Chat und Cowork), wartet die Frage
  stattdessen im Framework auf dich: ein Hinweis in der GUI (**Tools > Pending Approvals...**) und
  `llm-cli tools approvals` in einem Terminal. Gibst du sie dort frei, führt das Framework den Aufruf für den Client
  aus. Wo das Framework es anbieten kann, hat das Fenster auch „Approve and do not ask again“, eine dauerhafte
  Freigabe für Aufrufe in einem engen Rahmen, die du in Tool Permissions widerrufen kannst. Jeder Aufruf landet in
  einem manipulationssicheren Audit-Log (`llm-cli tools audit`).
- **Transporte:** stdio (Standard, vom Client gestartet) oder lokales Streamable HTTP
  (`llm-cli mcp serve --transport http`, nur Loopback, Standardadresse `http://127.0.0.1:8765/mcp`). Über HTTP
  braucht jeder Client sein eigenes benanntes Token: `llm-cli mcp token add NAME` zeigt es einmal, und der Client
  schickt es als `Authorization: Bearer <Token>`. Webseiten (Anfragen mit `Origin`-Header) werden abgewiesen.
- **Ein Token pro Client:** Ein benanntes Token ist der Aufrufer im Audit-Log (`mcp:<Name>`) und hat eigene
  Berechtigungen; ein neues darf nur lesen, bis du mehr erlaubst (`llm-cli tools permissions --token NAME`, oder in
  der GUI unter Tools > Tool Permissions (Ditto / MCP), wo Tokens auch angelegt, rotiert und widerrufen werden). Ein
  stdio-Client bekommt sein eigenes Token mit `LCF_MCP_TOKEN` in seinem Server-Eintrag; ohne es teilen sich alle
  lokalen stdio-Clients deine eigenen Berechtigungen.
- **Ressourcen:** Die Targets, die Capabilities eines Targets, Status und Log eines Builds, deine
  Tool-Berechtigungen und die Dokumentation des Frameworks lassen sich auch als Ressourcen anhängen
  (`lcf://targets`, `lcf://builds/<ID>/log`, `lcf://docs/Features`, ...). Sie folgen den Berechtigungen des
  passenden Lese-Tools und landen wie ein Tool-Aufruf im Audit-Log; Clients mit Protokoll 2026-07-28 erfahren, wenn
  sich ein Build oder die Berechtigungen ändern.
- **Prompts:** Dittos Aufgaben für deinen Client, mit denselben Anweisungen und demselben Wissenskontext, den Ditto
  bekommt: `ask_about_framework`, `convert_model_for_board`, `recommend_build_settings` und `diagnose_build_failure`
  (in Claude Code als Slash-Befehle). Sie gewähren nichts; jeder Schritt läuft weiter über die Tools und deine
  Berechtigungen, und ein Prompt zeigt nichts, was ein Lese-Tool, das du verbietest, zeigen würde.
- Ein Tool zum Übertragen auf Boards gibt es über MCP nicht.

**Ein Claude-Abo nutzen:** Anthropic erlaubt Drittanbieter-Apps nicht, die Anmeldung oder die Limits eines
Claude-Abos zu verwenden. Wenn du ein Claude-Abo hast, nutze das Framework **aus Claude Code oder Claude Desktop
über MCP** wie oben gezeigt, oder gib Ditto einen **Anthropic-API-Schlüssel** (Max- und Team-Abos enthalten
monatliches API-Guthaben für eine verknüpfte Claude-Console-Organisation).

---

## Ditto, der eingebaute Assistent

**Ditto ist optional und experimentell. Nutzung auf eigene Gefahr und Verantwortung. Der Ersteller von LLMCF haftet
nicht.** Das wiederholt den Gewährleistungsausschluss in [LICENSE.txt](LICENSE.txt) und fügt ihm nichts hinzu. Alles
andere im Framework funktioniert ohne Ditto. Der Chat-Bereich und die KI-Einstellungen zeigen ein Abzeichen
**Experimentell**; wenn du Ditto zum ersten Mal nutzt, sagt ein Hinweis das einmal (je Benutzer gespeichert), und
`llm-cli ditto` und `llm-cli module generate-ai` geben ihn an einem Terminal einmal aus.

Ditto beantwortet Fragen, schlägt Build-Einstellungen vor, analysiert gescheiterte Builds und schlägt neue
Hardware-Module vor. Dabei nutzt es die Tools des Frameworks im Rahmen deiner Tool-Berechtigungen:

- **GUI:** der **Chat-Bereich rechts** im Hauptfenster (F9 zeigt oder verbirgt ihn, Strg+Umschalt+C schreibt
  hinein; siehe [Der Chat-Bereich](docs/Features_DE.md#der-chat-bereich)). Tools, die eine Bestätigung brauchen,
  öffnen einen Dialog mit dem Tool und jedem Argument. Das Gespräch wird pro Benutzer gespeichert
  (`~/.llm-framework/chat_history.json`); ein langes Gespräch wird in ein neues zusammengefasst. **Chat leeren…**
  zählt zuerst auf, was es löscht (vollständiger Pfad, Dateiname, Größe, was es ist), mit „Nein“ als Vorgabe.
- **CLI:** `llm-cli ditto ask "Welche Boards können RKLLM?"` stellt eine einzelne Frage (ohne gespeicherten
  Verlauf). Bestätigungen werden im Terminal abgefragt und ohne Terminal abgelehnt. `--no-tools` antwortet ohne
  Tools.
- **Modulvorschläge:** `llm-cli module generate-ai target_hardware_config.txt` oder der Assistent unter **Tools >
  Create Hardware Module...**. Du siehst den ganzen Vorschlag, bevor etwas geschrieben wird.

**Ditto einrichten** unter **Tools > Configure AI Agent (Ditto)...**: Anbieter, Modell (die Liste wird live beim
Anbieter abgefragt; du kannst auch einen Modellnamen eintippen), API-Schlüssel, Workspace-ID (nur für
Claude-API-Schlüssel, die in jedem Workspace der Organisation gelten), Basis-URL (Ollama und eigene Server), Aufwand (Effort), Datenschutzstufe für Cloud-Anbieter, eine Ersatzkette, die Wissensbasis und der
Offline-Modus. **Anmelden...** neben dem Schlüsselfeld zeigt die Anmeldeverfahren des Anbieters mit Prüfen,
Speichern und Abmelden. Auf einem Rechner ohne Bildschirm meldest du dich mit `llm-cli ditto login <anbieter>` an
(der Schlüssel wird ohne Anzeige abgefragt oder mit `--with-token` von stdin gelesen) und setzt `ai_provider` in
der `config.yml` (oder die Umgebungsvariable `LLM_FRAMEWORK_AI_PROVIDER`).

| Anbieter (ID) | Was es ist | Anmeldung | Auf der Kommandozeile |
|---|---|---|---|
| `anthropic` | Anthropic-Claude-API | API-Schlüssel (ein Profil aus `ant auth login` als erweiterte Möglichkeit) | `llm-cli ditto login anthropic` |
| `openai` | OpenAI (Responses API) | API-Schlüssel | `llm-cli ditto login openai` |
| `google` | Google Gemini (OpenAI-kompatibler Endpunkt) | API-Schlüssel | `llm-cli ditto login google` |
| `openrouter` | OpenRouter (viele Modelle hinter einem Schlüssel) | API-Schlüssel | `llm-cli ditto login openrouter` |
| `ollama` | Ollama auf diesem Rechner oder in deinem Netz | keine | `llm-cli ditto login ollama --base-url URL` |
| `local` | der verwaltete lokale Modellserver (siehe [Offline-Modus](#offline-modus-das-verwaltete-lokale-modell)) | keine (ein Schlüssel pro Start wird automatisch verwaltet) | – |
| `custom` | jeder OpenAI-kompatible Server (vLLM, LM Studio, ...) | API-Schlüssel oder keine | `llm-cli ditto login custom --method api_key --base-url URL` |

- Die Anbieter sind Daten in [`configs/llm_providers.yml`](configs/llm_providers.yml); eine Datei mit demselben
  Namen in deinem Einstellungsordner ergänzt oder ersetzt Einträge. Dort stehen auch die Standardmodelle pro
  Aufgabe.
- **Datenschutzfilter für Cloud-Anbieter** (Einstellung `ai_security_level`): Geheimnisse werden immer entfernt.
  *Balanced* ersetzt zusätzlich IP-, MAC- und E-Mail-Adressen, Logins und Benutzernamen; *Strict* (Standard)
  außerdem Hostnamen, Seriennummern, UUIDs und Verzeichnisnamen. Ditto setzt die echten Werte auf deinem Rechner
  wieder in die Antwort ein. Lokale Anbieter werden nie gefiltert.
- **Ersatzkette** (Einstellung `ai_fallback_chain`, z. B. `local, ollama:qwen3.5:4b`): wird der Reihe nach
  versucht, wenn das Modell ablehnt oder der Anbieter nicht erreichbar ist.
- Strukturierte Antworten (Healing-Analyse, Modulvorschlag, Build-Empfehlung) müssen einer festen Struktur
  entsprechen und werden geprüft; eine ungültige Antwort ist ein Fehler und wird nie angewendet. Ein Vorschlag mit
  einem gefährlichen Befehl (zum Beispiel einem Download, der direkt in eine Shell geleitet wird) wird verworfen.
- **Anmeldung pro Anbieter:** **Anmelden...** in den KI-Einstellungen und `llm-cli ditto login` nehmen einen
  Schlüssel aus einem verdeckten Feld, von stdin oder aus der Umgebungsvariable des Anbieters, prüfen ihn kostenlos,
  bevor sie ihn speichern, und zeigen nur seine letzten vier Zeichen; `llm-cli ditto auth status` listet die
  Anmeldung jedes Anbieters. Anmeldung im Browser (OAuth) und Cloud-Plattformen kommen später. Bei `anthropic` hat der gespeicherte API-Schlüssel immer Vorrang; ein Profil aus
  `ant auth login` wird nur genutzt, wenn du es auswählst (Einstellung `ai_auth_anthropic: ant_profile`).
  Admin-API-Schlüssel von Claude (`sk-ant-admin01-`) werden abgelehnt. Ohne gespeicherten Schlüssel wird ein
  Schlüssel aus `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` oder `OPENROUTER_API_KEY` genutzt, und die
  KI-Einstellungen nennen die Variable. Ein Schlüssel eines `custom`-Servers gehört zu der Adresse, für die er
  gespeichert wurde, und geht nach einer Änderung der Basis-URL nicht mehr mit.
- Einen Anbieter für ein Claude-Abo gibt es nicht: Anthropic erlaubt Drittanbieter-Apps nicht, Abo-Limits zu nutzen.
  Ein gespeicherter Anbieter `claude_code` bricht mit einer Meldung ab, die die Alternativen nennt; nutze MCP oder
  einen API-Schlüssel wie [oben](#schnellstart-ki-clients-über-mcp-claude-code-claude-desktop-vs-code) beschrieben.
- `OPENAI_CUSTOM_HEADERS` und `ANTHROPIC_CUSTOM_HEADERS` aus der Umgebung werden (mit einer Warnung) ignoriert, damit
  sie deinen Schlüssel nicht ersetzen können; die Einstellung `ai_allow_sdk_env_headers: true` behält sie.

`llm-cli ditto eval` bewertet Ditto anhand fester Testfälle, zum Beispiel um zu prüfen, ob ein kleines lokales
Modell gut genug ist; `--kind retrieval` bewertet stattdessen die Suche in der Wissensbasis. Einzelheiten und
Messungen: [docs/Features_DE.md](docs/Features_DE.md#ditto-evaluation).

**Dittos Tools sind die dieses Computers.** Ditto nutzt die eigenen Tools des Frameworks auf diesem Computer,
dieselben, die ein MCP-Client bekommt, unter Dittos eigenen Berechtigungen. Es verbindet sich nie mit einem Framework
auf einem anderen Computer (eine frühere Entwicklungsversion konnte das; dieser Modus wurde entfernt, siehe
[docs/Features_DE.md](docs/Features_DE.md#nur-die-tools-dieses-computers)).

---

## Offline-Modus: das verwaltete lokale Modell

Ditto kann ganz ohne Cloud-Konto mit einem lokalen Modell arbeiten. Das Framework baut `llama-server` auf deinem
Rechner aus dem festgelegten llama.cpp-Commit, lädt ein festgelegtes Qwen3.5-Modell herunter und betreibt den Server:

```bash
llm-cli ditto runtime status       # Hardware, der genutzte Build, das empfohlene Modell, Serverzustand
llm-cli ditto runtime install      # baut llama-server für diesen Rechner (zuerst den CPU-Build, dann den GPU-Build)
llm-cli ditto models list          # die Modellstufen, ihre Größe und ob sie heruntergeladen sind
llm-cli ditto models download      # das empfohlene Modell (oder eine Stufe, z. B. small-q2)
llm-cli ditto runtime start        # startet llama-server; er läuft weiter bis „runtime stop“
llm-cli ditto runtime stop
```

**Der Build.** `runtime install` zeigt, was es bauen wird, und fragt vorher. Es braucht einen C/C++-Compiler (GCC
oder Clang), CMake und Ninja (unter Debian oder Ubuntu: `build-essential cmake ninja-build`); ohne sie baut es im
Builder-Container des Frameworks, wenn Docker läuft. Das Framework installiert nie eine Toolchain für dich. Der
festgelegte Quellcode (etwa 215 MB) wird einmal über seinen Commit geholt und geprüft; der Build selbst läuft ohne
Netzwerk. Der CPU-Build kommt zuerst und dauert ein paar Minuten, damit `runtime start` bald funktioniert; ein
GPU-Build (in dieser Version Vulkan unter Linux; CUDA-, ROCm-, Windows- und macOS-Builds folgen) wird danach gebaut
und genutzt, sobald er seine Prüfungen bestanden hat. Jeder Build wird auf diesem Rechner geprüft, bevor er behalten
wird (Version und Commit der Festlegung, die gefundenen Geräte, und dass der Server eigene Tools ablehnt), und
`runtime start` startet nichts anderes.

- **Rechner ohne Netzwerk:** `llm-cli ditto runtime export-source DATEI` und `import-source DATEI` tragen den
  Quellcode; `llm-cli ditto runtime export DATEI.tar.gz` auf einem Rechner derselben Art (gleiches Betriebssystem und
  gleiche Architektur, und eine CPU desselben Herstellers mit mindestens den CPU-Funktionen dieses Rechners, weil der
  Build `-march=native` nutzt) und `llm-cli ditto runtime import DATEI.tar.gz` hier tragen einen fertigen Build. Ein
  importierter Build wird gegen die Festlegung und sein eigenes Manifest geprüft, du bestätigst ihn, und er wird hier
  geprüft, bevor er genutzt wird.
- **Offizielles vorgebautes Archiv:** `llm-cli ditto runtime install --prebuilt` lädt stattdessen das Archiv von
  upstream herunter (Größe und sha256 geprüft). Es wird nur genutzt, wenn du es verlangst, auch ein Archiv, das
  eine frühere Version heruntergeladen hat (dann übernimmt `--prebuilt` es, ohne es neu herunterzuladen), und immer
  als „prebuilt, not self-built“ (vorgebaut, nicht selbst gebaut) angezeigt. Unter Linux braucht es die glibc, für
  die es gebaut wurde (2.35 oder 2.39); auf musl-Systemen wie Alpine läuft nur der selbst gebaute Server. Bis die
  Windows- und macOS-Builds kommen, ist das dort der Weg zum lokalen Modell.
- `llm-cli ditto runtime prune` listet alte Builds, damit du die löschst, die du auswählst (nichts ist angehakt; das
  Framework löscht keinen Build von selbst); der aktive Build, sein CPU-Ersatz, der Build, aus dem ein laufender
  Server gestartet wurde, und die Builds deiner Boards stehen nie in der Liste. `runtime status` sagt, wann ein neuer
  Build nötig (die Festlegung hat sich geändert) oder empfohlen ist (der Bauplan dieses Rechners hat sich geändert).

**Direkt oder in Docker.** Der Server läuft standardmäßig direkt auf dem Rechner (ohne den Aufwand von Docker) oder
in Docker:

```bash
llm-cli ditto runtime ensure                              # installiert ihn, wenn er fehlt; ein zweiter Lauf tut nichts
llm-cli ditto runtime ensure --mode docker --force        # zu Docker wechseln (braucht ein Terminal)
llm-cli ditto runtime ensure --mode bare-metal --force    # und zurück
```

- **Das Image** wird auf deinem Rechner aus demselben festgelegten llama.cpp-Rezept gebaut, auf einem Docker Official
  Image als Basis (Debian, Ubuntu oder Alpine, per Digest festgelegt). Es wird nie als fertiges Image geholt und nie
  hochgeladen. Bevor es benutzt wird, wird es in einem Container aus ihm selbst geprüft (Version und Commit der
  Festlegung, die Geräte, die es sieht, und dass der Server seine eigenen Werkzeuge ablehnt).
- **Der Container** hält dieselben Regeln ein wie der direkte Server: Sein Port wird nur auf 127.0.0.1
  veröffentlicht, der Modellordner wird schreibgeschützt eingebunden (die Modelle werden nicht kopiert), er läuft als
  dein Benutzer ohne zusätzliche Rechte, und er bekommt bei jedem Start eine neue Schlüsseldatei. Die GPU wird
  durchgereicht, wo das geht (NVIDIA über das NVIDIA Container Toolkit, AMD und Intel über `/dev/dri`); wo nicht,
  sagt `ensure` warum, und der Container rechnet auf der CPU.
- **Ein Wechsel** behält die Modelle (nichts wird noch einmal heruntergeladen) und erzeugt nur den Server neu: Der
  direkte Build kommt ins Image, wenn die C-Bibliothek des Basis-Images passt, und der Build des Images wird zum
  direkten Build, sobald er seine Prüfungen auf diesem Rechner bestanden hat. Wo das nicht geht, sagt `ensure` warum
  und baut neu.
- **Die Dateien der alten Art** (Build-Ordner mit vollständigem Pfad, Docker-Images und -Container mit Name und ID,
  jeweils mit Größe) werden nach dem Wechsel aufgelistet, und nur, was du bestätigst, wird entfernt („Nein“ ist
  vorgewählt). Ohne Terminal wird nichts entfernt. Docker-Images werden sonst nie gelöscht.
- `llm-cli ditto runtime status` zeigt die Art. Ein KI-Client darf sie lesen (`get_status`); wechseln und entfernen
  darfst nur du.

Wähle danach in den KI-Einstellungen **Local model (managed llama-server)** (Anbieter `local`). Schalte zusätzlich
den **Offline Mode** ein, wenn Ditto nie einen Cloud-Anbieter nutzen soll. Ditto findet den laufenden Server von
selbst. Die KI-Einstellungen zeigen, wo der Server läuft, und können ihn umstellen; gestartet und gestoppt wird er
über die Kommandozeile.

| Stufe | Modell | Download | Automatisch gewählt, wenn |
|---|---|---|---|
| `large` | Qwen3.5 9B, 4 Bit (Q4_K_M) | 5,3 GiB | eine eigene GPU (oder Apple Silicon) mit mindestens 7,5 GiB Speicher genutzt wird |
| `default` | Qwen3.5 4B, 4 Bit (Q4_K_M) | 2,6 GiB | sonst |
| `small` | Qwen3.5 2B, 4 Bit (Q4_K_M) | 1,2 GiB | bis 4,5 GiB RAM, oder wenn `default` nicht passt |
| `small-q2` | Qwen3.5 2B, 2 Bit (UD-Q2_K_XL) | 0,9 GiB | bis 3 GiB RAM; ungenauer |
| `default-q2`, `large-iq2`, `granite` | 2-Bit-Varianten und IBM Granite 4.2 3B | 1,8 / 3,4 / 2,1 GiB | nur auf Wunsch |

- `llm-cli ditto runtime start --model <Stufe>` wählt eine Stufe und merkt sie sich; `--model auto` kehrt zur
  Empfehlung zurück.
- **Hardware:** Das Bau-Rezept folgt Betriebssystem, Architektur und C-Bibliothek des Rechners. AMD-Radeon-GPUs und
  -APUs (z. B. Ryzen Z1 Extreme) sowie Intel Arc bekommen einen Vulkan-Build; NVIDIA-GPUs werden einen CUDA-Build
  bekommen, der zu GPU und Treiber passt (bis dahin läuft dort der CPU-Build, und `runtime install --backend vulkan`
  baut einen Vulkan-Server, der auch NVIDIA-GPUs nutzt); alles andere nutzt den CPU-Build. `runtime status` begründet
  jede Wahl.
- **Geprüfte Downloads:** Der llama.cpp-Commit, die vorgebauten Archive und jedes Modell sind in
  [`configs/llama_runtime.yml`](configs/llama_runtime.yml) festgelegt (Archive und Modelle mit Größe und sha256,
  Modelle auf einem festen Hugging-Face-Commit) und werden geprüft, bevor sie genutzt werden; Einträge ohne diese
  Angaben werden abgelehnt.
- **Abschottung:** Der Server lauscht nur auf 127.0.0.1, auf einem freien Port, mit einem neuen API-Schlüssel pro
  Start, der in einer Datei übergeben wird, die nur du lesen kannst, und mit einer geleerten Umgebung. Seine
  Weboberfläche, seine eigenen Tools und sein MCP-Client bleiben aus; der selbst gebaute Server wird ganz ohne sein
  Shell-Tool kompiliert.
- **Aufwand:** Mit dem Aufwand „low“ antwortet das lokale Modell, ohne vorher nachzudenken (auf einer CPU viel
  schneller); „medium“ und höher lassen es nachdenken.

Ollama oder ein anderer OpenAI-kompatibler Server in deinem Netz zählen im Offline-Modus ebenfalls als lokale
Anbieter.

---

## Die Wissensbasis

Die optionale Wissensbasis lässt Ditto und MCP-Clients in Dokumentation suchen, die du aufgenommen hast (zum
Beispiel SDK-Handbücher). Du schaltest sie in den KI-Einstellungen ein (**Enable Local Knowledge Base**). Alles
läuft auf deinem Rechner:

- Embeddings werden lokal mit FastEmbed (ONNX Runtime) berechnet; das Modell wird einmal nach
  `models/embeddings/` geladen (etwa 0,5 GB auf einem Rechner ohne CUDA); im Offline-Modus wird nichts
  heruntergeladen,
- die Vektoren liegen in einer eingebetteten Qdrant-Datenbank in `~/.llm-framework/qdrant`, ohne Docker und ohne
  offenen Port,
- die Suche verbindet Bedeutung (Vektoren) mit exakten Wörtern (BM25), sodass auch Fehlercodes und Compiler-Flags
  gefunden werden, auch Teile von Bezeichnern wie `GGML_CUDA` in `-DGGML_CUDA=ON`,
- ein lokaler Cross-Encoder bewertet die Auszüge für Build-Empfehlungen neu und wirft unpassende weg (Einstellung
  `rag_reranker`).

Dokumentation nimmst du mit **Deep Ingest (Add Docs)** im Modul-Assistenten auf (**Tools > Create Hardware
Module...**), oder ein KI-Client oder Ditto bittet mit `crawl_docs_site` um einen Crawl (du bestätigst jeden und siehst
jede Startadresse). Vor deinem ersten Crawl sagt ein einmaliger Hinweis, was gelesen und behalten wird; er ist eine
Information, keine Bestätigung. Der Crawler sperrt private Netzwerkadressen, begrenzt Seitenzahl und Tiefe und
beachtet diese maschinenlesbaren Opt-outs einer Website: robots.txt, den Header `X-Robots-Tag`, robots-Meta-Tags
(auch `noai`) und das TDM Reservation Protocol. Keine dieser Prüfungen lässt sich abschalten. Der Assistent zeigt
„Seite N von M“, **Stoppen** behält, was schon gespeichert ist, und das Ergebnis lautet Erfolg, teilweise oder
Fehlschlag; das Ergebnis spricht auch die Sprachausgabe des Betriebssystems, wenn es eine gibt, außer du schaltest
das aus. Suchergebnisse gelten als externer Text, nie als Anweisungen. Jede Seite hält auch ihre Lizenz und ihren
Urheberrechtshinweis fest: Ein Wissenspaket enthält den vollen Text einer Seite nur, wo ihre Lizenz die Weitergabe
erlaubt, jede andere Seite als ihre Adresse mit kurzen Fakten (`llm-cli knowledge sources` zeigt jede Seite). Mehr
dazu: [Wissensbasis](docs/Features_DE.md#wissensbasis-rag), [Deep Ingest](docs/Features_DE.md#deep-ingest) und
[Lizenzen und Urheberrecht](docs/Features_DE.md#lizenzen-und-urheberrecht).

---

## Community-Zoo

Targets, Rezepte (wie ein Modell für ein Board gebaut wurde) und Wissen lassen sich als signierte Packs teilen:
`llm-cli zoo list|show|install|verify|export|keys` oder **Tools > Community Hub öffnen**. Ein Pack wird erst nach
Prüfungen in einem Quarantäne-Ordner und deiner Bestätigung installiert und zeigt eine Vertrauensstufe. Deine eigene
Arbeit wird datenschutzbereinigt exportiert und mit deinem Schlüssel signiert (Wissen mit dem vollen Text einer Seite
nur, wo ihre Lizenz es erlaubt, und der Export führt jede Seite auf, mit dem, was sie enthält, und warum); ein Pull
Request läuft über die GitHub CLI oder den Browser, und das Framework fragt nie nach einem Token. Packs lassen sich
auch für deine eigenen Rechner oder ein Team verschlüsseln, im Format age, sodass auch die üblichen Werkzeuge age und
rage sie öffnen. Ein Community-Repository oder ein Index ist noch nicht veröffentlicht; heute lassen sich deshalb nur
Pack-Dateien installieren. Einzelheiten: [docs/Features_DE.md](docs/Features_DE.md#community-zoo).

---

## Sicherheit in Kürze

- **Builds in einer Sandbox:** Konvertierungs-Container laufen als dein Benutzer statt als root, ohne
  Netzwerkzugang, ohne Linux-Capabilities, mit `no-new-privileges`, schreibgeschütztem Image-Dateisystem und einem
  Prozesslimit; ein Speicherlimit lässt sich setzen. Modelle werden vorher auf dem Host heruntergeladen.
- **Tool-Berechtigungen und Audit-Log:** Ditto und MCP-Clients handeln nur über registrierte Tools mit
  Risikokategorien. Du entscheidest, was selbst läuft, was fragt und was verboten ist. Jeder Aufruf landet in einem
  HMAC-verketteten Audit-Log; `llm-cli tools audit --verify` erkennt geänderte oder entfernte Einträge.
- **SSH-Vertrauen beim ersten Kontakt:** Der Host-Schlüssel eines unbekannten Boards wird mit seinem
  SHA256-Fingerabdruck angezeigt und erst nach deiner Bestätigung gespeichert. Ein geänderter Schlüssel wird immer
  abgelehnt. Das SSH-Passwort dient einmal dazu, den eigenen Schlüssel des Frameworks zu hinterlegen, und wird nie
  gespeichert.
- **Geheimnisse:** API-Schlüssel und Tokens werden verschlüsselt in einem Tresor in deinem persönlichen Ordner
  gespeichert; sein Schlüssel liegt im Schlüsselbund deines Betriebssystems. Ohne nutzbaren Schlüsselbund wird nichts
  für dich gewählt: Das Framework hält an und nennt die Möglichkeiten (Einstellung `secret_storage`: ein Befehl als
  Credential-Helper, eine Passphrase, eine unverschlüsselte Schlüsseldatei oder nur der Arbeitsspeicher).
  Zugangsdaten werden in jedem Log geschwärzt, auch mit `--debug-http`. `llm-cli mojo` warnt, wenn der Schlüssel
  des Tresors nicht geschützt ist.
- **Nicht vertrauenswürdiger Text:** Webseiten, Texte aus der Wissensbasis, Build-Logs, Tool-Ergebnisse und
  Probe-Dateien werden von versteckten Zeichen bereinigt und der KI als gekennzeichnete Daten übergeben, nie als
  Anweisungen. Chat-Ausgaben werden maskiert, und Links öffnen sich erst nach deiner Bestätigung.
- **Datenschutz:** Bei Cloud-Anbietern werden persönliche Daten durch Platzhalter ersetzt, bevor sie deinen
  Rechner verlassen (siehe [Ditto](#ditto-der-eingebaute-assistent)).

Einzelheiten: [docs/Features_DE.md](docs/Features_DE.md).

---

## Einstellungen

Die GUI speichert ihre Einstellungen in der `config.yml` im Einstellungsordner (siehe
[Wo deine Dateien liegen](#wo-deine-dateien-liegen)). Du kannst die Datei auch selbst bearbeiten oder einzelne
Werte mit Umgebungsvariablen der Form `LLM_FRAMEWORK_<SCHLÜSSEL>` überschreiben, zum Beispiel
`LLM_FRAMEWORK_BUILD_MEMORY_LIMIT=24g`.

Einige Einstellungen mit ihren Standardwerten:

```yaml
language: en                  # en oder de
ai_provider: OpenAI           # Anbieter-ID aus der Tabelle oben, z. B. local; alte Namen wie OpenAI gehen auch
ai_security_level: STRICT     # STRICT, BALANCED oder OFF (nur Cloud-Anbieter)
offline_mode: false           # true: nur lokale Anbieter
secret_storage: keyring       # keyring, helper, passphrase, insecure oder ephemeral (Schutz des Secrets-Tresors)
enable_rag_knowledge: false   # die lokale Wissensbasis
calibration_language: mixed   # mitgelieferter Kalibrierungstext: mixed, de oder en
build_sandbox: true           # Sandbox für Konvertierungs-Container
build_memory_limit: ""        # z. B. 24g; leer = kein Limit
build_pids_limit: 4096        # Prozesslimit im Container
build_jobs: 0                 # parallele Compile-Jobs beim Image-Build; 0 = alle Kerne
crawler_max_depth: 2          # Linktiefe, mit der sich der Deep-Ingest-Dialog öffnet
rag_reranker: auto            # auto (nach Hardware gewählt), off oder die ID eines Cross-Encoder-Modells
rag_rerank_default: false     # true: auch Chat- und MCP-Suchen neu bewerten (Standard: nur Build-Empfehlungen)
# rag_rerank_min_score: 0.3   # Relevanzschwelle 0..1; ohne Wert = kalibrierte Schwelle des Modells (0,2), 0 = keine
rag_model_idle_minutes: 10    # Modelle der Wissensbasis nach dieser Leerlaufzeit entladen; 0 = geladen lassen
audio_announcements: true     # gesprochener Deep-Ingest-Hinweis und gesprochenes Ergebnis (Tools > Gesprochene Ansagen)
community_index_url: ""       # https-Adresse des signierten Community-Index; leer, bis einer veröffentlicht ist
community_repo: ""            # GitHub owner/name, das Community-Packs als Pull Requests bekommt; vorerst leer
```

Die Einstellungen zu Wissensbasis, Deep Ingest und Community erklärt [docs/Features_DE.md](docs/Features_DE.md).

---

## Bekannte Lücken

- **Das Framework im Container baut keine Modelle:** Das Image, das du mit `make build` baust, enthält kein
  Docker; Konvertierungen laufen deshalb auf einem Computer, auf dem das Framework direkt installiert ist
  ([docs/Container_Setup_DE.md](docs/Container_Setup_DE.md#8-grenzen)).
- **Windows-Hosts:** Die Testsuite läuft in der CI unter Windows, Konvertierungen auf einem Windows-Host sind aber
  nicht geprüft.
- **Anmeldung im Browser und bei Cloud-Plattformen** (OAuth, AWS, Google Cloud, Azure) gibt es noch nicht; Ditto
  meldet sich mit API-Schlüsseln an, lokale Engines brauchen keine Anmeldung, und ein Profil aus `ant auth login`
  ist eine erweiterte Möglichkeit.
- **Deep Ingest** hat keinen CLI-Befehl: Du startest es im Modul-Assistenten in der GUI oder bestätigst das
  `crawl_docs_site` eines KI-Clients.
- **Community-Zoo:** Ein Community-Repository, ein Index oder ein Maintainer-Schlüssel ist noch nicht veröffentlicht
  (`configs/community_keys.yml` ist leer, `community_index_url` und `community_repo` sind leer); installieren lassen
  sich deshalb nur Pack-Dateien.

---

## Wie es weitergeht

Die Roadmap führt auf, was geplant ist; Erledigtes wandert beim Release in den [CHANGELOG](CHANGELOG_DE.md). Kurz:

- **In Arbeit:** die übrigen Anmeldeverfahren je Zugangsart, Anmeldung im Browser und Cloud-Plattformen
  (API-Schlüssel, lokale Engines und das `ant`-Profil funktionieren in der GUI und mit `llm-cli ditto login`; der
  Claude-Abo-Anbieter ist entfernt, und Claude-Abonnenten nutzen MCP oder einen API-Schlüssel); eine neue Ditto-Evaluation mit den verbesserten Prompts; die selbst gebaute llama.cpp-Laufzeit auf dem
  Board (die Kommandozeile funktioniert; die GUI, die MCP-Werkzeuge und ein Lauf auf einem echten Board folgen).
- **Geplant:** Rezepte als Target-Vorlagen, direkt danach eine Rotationsstufe vor der Quantisierung (SpinQuant,
  QuaRot; nur wo das Format sie unterstützt) und ein allgemeines ARM64-Target für den Raspberry Pi; Targets für
  NVIDIA-GPUs und AMD (Ryzen Z1 Extreme); später Modelle der Llama-Familie in jedem Format, ONNX, Hailo, Intel und
  Qualcomm; LoRA und große Modelle sowie eine gehärtete Lieferkette. Neue Targets beginnen mit der Hardware-Probe, und
  ein Benchmark auf dem Board ist der Beweis („getestet auf <Board>“). Builds in der Cloud sind nicht geplant.

Vollständige Liste: [docs/BLUEPRINT_DE.md](docs/BLUEPRINT_DE.md#roadmap).

---

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [docs/CLI_Usage_DE.md](docs/CLI_Usage_DE.md) | Alle CLI-Befehle und Optionen mit Beispielen |
| [docs/Features_DE.md](docs/Features_DE.md) | Funktionen im Detail: Build-Pipeline, Sandbox, Ditto, Evaluation, lokale Laufzeit, Wissensbasis, Deep Ingest, MCP, Community-Zoo |
| [docs/BLUEPRINT_DE.md](docs/BLUEPRINT_DE.md) | Architektur und Roadmap |
| [docs/Module_Developer_Guide_DE.md](docs/Module_Developer_Guide_DE.md) | Ein neues Hardware-Target hinzufügen |
| [docs/Container_Setup_DE.md](docs/Container_Setup_DE.md) | Das Framework in einem Container, den du selbst baust (Server, NAS, Proxmox) |
| [docs/Mojo_DE.md](docs/Mojo_DE.md) | Der Gesundheitscheck: Mojo Oracle schaut, Mojo Miracle repariert (früher der Doctor) |
| [CHANGELOG_DE.md](CHANGELOG_DE.md) | Was sich in jeder Version geändert hat |
| [CONTRIBUTING_DE.md](CONTRIBUTING_DE.md) | Entwicklungsumgebung, Tests, Konventionen |
| [configs/SSOT_ARCHITECTURE_DE.md](configs/SSOT_ARCHITECTURE_DE.md) | Projektquellen (Single Source of Truth) |
| [data/calibration/README_DE.md](data/calibration/README_DE.md) | Mitgelieferte Kalibrierungstexte: Quellen, Prüfsummen, Lizenz |

Jedes Dokument gibt es auch auf Englisch (ohne `_DE` im Namen).

## Mitmachen

Beiträge sind willkommen, besonders neue Hardware-Targets. Wie du eine Entwicklungsumgebung einrichtest, die Tests
ausführst und Änderungen einreichst: [CONTRIBUTING_DE.md](CONTRIBUTING_DE.md).

## Danksagung

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)**: GGUF-Konvertierung, Quantisierung und der lokale
  Modellserver
- **[Hugging Face](https://huggingface.co/)**: das Modell-Ökosystem
- **[Rockchip RKNN-LLM](https://github.com/airockchip/rknn-llm)** und
  **[RKNN-Toolkit2](https://github.com/airockchip/rknn-toolkit2)**: die NPU-Toolkits
- **[Qwen](https://huggingface.co/Qwen)** und **[IBM Granite](https://huggingface.co/ibm-granite)**: die Modelle
  für Dittos Offline-Modus
- **[Ditto](https://github.com/yoheinakajima/ditto)** von [@yoheinakajima](https://github.com/yoheinakajima):
  KI-Agenten-Framework und Namensgeber des Assistenten
- **[Model Context Protocol](https://modelcontextprotocol.io)**: das Python-SDK hinter dem MCP-Server
- **[eaddario/imatrix-calibration](https://huggingface.co/datasets/eaddario/imatrix-calibration)**: die
  mitgelieferten Kalibrierungstexte (MIT)
- **[Qdrant](https://qdrant.tech/)** und **[FastEmbed](https://github.com/qdrant/fastembed)**: die lokale
  Wissensbasis
- **[rag4ll](https://github.com/Smilez1985/rag4ll)**: Der Reranker, das Budget für Auszüge, das Manifest der Packs,
  die Datenschutz-Bereinigung und der Aufbau des Sprachaufrufs sind daraus übernommen (MIT, siehe
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md))
- **[Radxa Community](https://forum.radxa.com/)**: Unterstützung bei der Integration des RK3566
- **[Docker](https://www.docker.com/)**, **[PySide6](https://doc.qt.io/qtforpython-6/)**,
  **[uv](https://docs.astral.sh/uv/)**

## Lizenz

MIT-Lizenz, siehe [LICENSE.txt](LICENSE.txt). Die Datei enthält außerdem zusätzliche Bedingungen zu Namensnennung,
Software Dritter, Hersteller-SDKs und Modelllizenzen. Modelle, die du konvertierst, behalten ihre eigenen Lizenzen.
