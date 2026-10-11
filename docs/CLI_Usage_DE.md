Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md) · Zuletzt aktualisiert am 2026-10-11

[English](CLI_Usage.md) · **Deutsch**

# Kommandozeile (`llm-cli`)

Die Kommandozeile kann alles, was du auf einem Server ohne Bildschirm, in Skripten und in der CI brauchst: die
Installation prüfen, Modelle konvertieren, sie übertragen, Ditto und sein lokales Modell einrichten,
Tool-Berechtigungen verwalten und den MCP-Server betreiben. Jeder Befehl und jede Option unten stammt aus der
echten `--help`-Ausgabe dieser Version.

Zurück zur [README](../README_DE.md) · Funktionen im Detail: [Features_DE.md](Features_DE.md)

---

## Inhalt

- [Die CLI aufrufen](#die-cli-aufrufen)
- [Globale Optionen, Ausgabe und Exit-Codes](#globale-optionen-ausgabe-und-exit-codes)
- [Überblick über alle Befehle](#überblick-über-alle-befehle)
- [doctor](#doctor): Gesundheitscheck
- [targets](#targets): `list`, `validate`
- [build](#build): `start`, `status`
- [deploy](#deploy): `list-artifacts`, `package`, `run`
- [ditto](#ditto): `setup`, `ask`, `eval`, `runtime`, `models`
- [tools](#tools): `list`, `permissions`, `audit`
- [mcp](#mcp): `serve`, `config`
- [secrets](#secrets): `set`, `list`
- [module](#module): `create`, `generate-ai`
- [scan](#scan): Hardware-Probe
- [config](#config): `show`, `repos`
- [swarm](#swarm): `upload`
- [Bekannte Einschränkungen](#bekannte-einschränkungen)

---

## Die CLI aufrufen

| Installation | Befehl |
|---|---|
| Linux-Installer (`install.sh`) | `cd /opt/llm-conversion-framework`, dann `.venv/bin/python -m orchestrator.cli ...` |
| Windows-Installer (`install.bat`) | `cd /d "C:\Program Files\LLM-Conversion-Framework"`, dann `.venv\Scripts\python.exe -m orchestrator.cli ...` |
| Entwicklungsumgebung (`poetry install`) | `llm-cli ...` |

`python -m orchestrator.cli` braucht den Installationsordner im Suchpfad von Python. Ruf es entweder in diesem
Ordner auf oder setze `PYTHONPATH`. Unter Linux funktioniert dieser Alias aus jedem Ordner:

```bash
alias llm-cli='PYTHONPATH=/opt/llm-conversion-framework /opt/llm-conversion-framework/.venv/bin/python -m orchestrator.cli'
```

Die Beispiele unten verwenden `llm-cli`.

### Umgebungsvariablen

| Variable | Wirkung |
|---|---|
| `LLM_FRAMEWORK_CONFIG_DIR` | Einstellungsordner (`config.yml`). Standard: `configs/` in der Installation, wenn beschreibbar, sonst `~/.llm-framework`. Das Arbeitsverzeichnis wird nie verwendet. |
| `LLM_FRAMEWORK_HOME` | Ordner für den persönlichen Zustand. Standard: `~/.llm-framework`. |
| `LLM_FRAMEWORK_MODELS_DIR` | Modellordner; Dittos lokale Modelle liegen im Unterordner `ditto/`. Standard: `models/` in der Installation. |
| `LLM_FRAMEWORK_<SCHLÜSSEL>` | Überschreibt eine Einstellung für diesen Aufruf, z. B. `LLM_FRAMEWORK_AI_PROVIDER=local` oder `LLM_FRAMEWORK_BUILD_MEMORY_LIMIT=24g`. Es zählen nur bekannte Einstellungsnamen. |

---

## Globale Optionen, Ausgabe und Exit-Codes

```
llm-cli [OPTIONEN] BEFEHL [ARGUMENTE]...
```

| Option | Bedeutung |
|---|---|
| `--version` | Version ausgeben und beenden. |
| `-c, --config PATH` | Diese Einstellungsdatei verwenden (YAML oder JSON, muss existieren). Ihr Ordner wird für diesen Aufruf zum Einstellungsordner (eine `config.yml` dort wird ebenfalls gelesen), und die Werte der Datei haben Vorrang davor. Der übliche Einstellungsordner wird dann nicht gelesen. |
| `-v, --verbose` | Log-Level `DEBUG`. |
| `-q, --quiet` | Log-Level `ERROR` und kein Start-Banner. |
| `--log-level [DEBUG\|INFO\|WARNING\|ERROR]` | Log-Level (Standard `INFO`). |
| `--debug-http` | Die Debug-Ausgabe der HTTP-Bibliotheken und der SDKs der KI-Anbieter auf stderr zeigen und `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug` beibehalten. Ohne die Option bleiben diese Logger auch mit `--verbose` auf `INFO`, und die beiden Variablen werden auf `info` gesenkt. |
| `--help` | Hilfe für die CLI, eine Gruppe oder einen Befehl, z. B. `llm-cli ditto runtime start --help`. |

Globale Optionen stehen vor dem Befehl: `llm-cli -q doctor`, nicht `llm-cli doctor -q`.

Zugangsdaten (API-Schlüssel, Tokens, `Authorization`-, `x-api-key`- und Cookie-Header, Anmeldecodes und Tokens in
URLs) werden in jeder Logzeile und jedem Traceback geschwärzt, auch mit `--debug-http`.

**Ausgabe:** Das Start-Banner und Log-Meldungen gehen nach stderr, Ergebnisse nach stdout, ebenso die meisten
Meldungen, die ein Befehl selbst ausgibt, auch seine Fehlermeldungen. Bei `llm-cli ditto ask` geht nur die Antwort
nach stdout, und bei `llm-cli mcp serve` trägt stdout das MCP-Protokoll.

**Exit-Codes:**

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Der Befehl ist gescheitert (zum Beispiel ein gescheiterter Build, eine gescheiterte Übertragung, ein beschädigtes Audit-Log, ein KI-Fehler) |
| 2 | Ungültige Eingabe: unbekannte Option, ein unbekanntes Target oder eine nicht unterstützte Kombination aus Format, Board und Quantisierung (`build start`), ein unbekannter Anbieter (`--provider`), eine unbekannte Modellstufe (`ditto models download`) |
| 130 | `build start` wurde mit Strg+C unterbrochen (die CLI endet erst, wenn der Build zu Ende ist) |

`llm-cli doctor` verwendet 0 (OK), 1 (Warnungen) und 2 (Fehler); `llm-cli ditto eval --min-pass` liefert 1, wenn
ein Lauf unter dem angegebenen Anteil bleibt. Einige Befehle melden einen Fehler, enden aber trotzdem mit 0 (siehe
[Bekannte Einschränkungen](#bekannte-einschränkungen)).

---

## Überblick über alle Befehle

| Befehl | Was er tut |
|---|---|
| `doctor` | Die Installation prüfen und erklären, wie man Probleme behebt |
| `targets list` / `targets validate` | Hardware-Targets auflisten / prüfen, ob ein Target geladen werden kann |
| `build start` / `build status` | Ein Modell konvertieren und quantisieren / die Builds dieses Prozesses zeigen |
| `deploy list-artifacts` / `deploy package` / `deploy run` | Fertige Builds auflisten / einen Build in eine ZIP-Datei packen / sie auf ein Board laden und installieren |
| `ditto setup` | Die Tool-Berechtigungen festlegen (dieselbe Auswahl wie im Dialog beim ersten Start der GUI) |
| `ditto ask` | Ditto eine Frage stellen |
| `ditto eval` | Ditto anhand fester Testfälle bewerten |
| `ditto runtime install/start/status/stop` | Der verwaltete lokale Modellserver (llama.cpp) für den Offline-Modus |
| `ditto models list/download` | Die Modellstufen für den Offline-Modus |
| `tools list` / `tools permissions` / `tools audit` | Die Tools des Frameworks, ihre Berechtigungen und das Audit-Log |
| `mcp serve` / `mcp config` | Den MCP-Server betreiben / zeigen, wie man KI-Clients verbindet |
| `secrets set` / `secrets list` | Einen API-Schlüssel oder ein Token speichern / die gespeicherten Namen auflisten |
| `module create` / `module generate-ai` | Ein neues Hardware-Target anlegen, per Fragen oder mit Ditto aus einer Probe-Datei |
| `scan` | Die Hardware-Probe auf diesem Rechner ausführen |
| `config show` / `config repos` | Die Grundeinstellungen der CLI zeigen / Überschreibungen für Quell-Repositorys verwalten |
| `swarm upload` | Einen Export der Wissensbasis in das Community-Repository hochladen |

---

## doctor

```
llm-cli doctor [--json] [--fix] [--tier 1|2]
```

Prüft Python, den Docker-Daemon und buildx, den freien Speicherplatz für den Cache- und den Ausgabeordner,
die Targets und ihre Capabilities, ob der persönliche Ordner privat ist, den Secrets-Speicher und den
Schlüsselbund, die Tool-Berechtigungen, das Audit-Log und die mitgelieferten Kalibrierungsdaten. Zu jedem Befund
gibt es einen Hinweis, wie du ihn behebst.

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe (Befunde, Korrekturen, Exit-Code). |
| `--fix` | Die automatischen Korrekturen bis `--tier` anwenden, dann erneut prüfen. |
| `--tier INTEGER RANGE` | `1` (Standard): harmlose Korrekturen (die Hilfe nennt „permissions, folders“; in dieser Version heißt das, den persönlichen Ordner privat zu machen). `2`: entfernt auch Caches (verwaiste Framework-Images und den Docker-Build-Cache). |

Speicherplatz: Unter 20 GB frei gibt es eine Warnung, unter 5 GB einen Fehler.

```bash
llm-cli doctor
llm-cli doctor --json > doctor.json
llm-cli doctor --fix --tier 2
```

---

## targets

### targets list

```
llm-cli targets list [-f table|json|yaml]
```

Listet die Targets in `targets/` auf. Die Tabelle zeigt den Anzeigenamen des Targets (zum Beispiel „Rockchip
Family“), seine Architektur, ob es bereit ist, und seinen Ordner. **Der Wert für `--target` ist der Ordnername**,
zum Beispiel `Rockchip`.

| Option | Bedeutung |
|---|---|
| `-f, --format [table\|json\|yaml]` | Ausgabeformat (Standard `table`). |

### targets validate

```
llm-cli targets validate TARGET_NAME
```

Prüft, ob das Target geladen werden kann. Exit-Code 1, wenn es nicht gefunden wird.

```bash
llm-cli targets validate Rockchip
```

---

## build

### build start

```
llm-cli build start -m MODELL -t TARGET [Optionen]
```

Konvertiert und quantisiert ein Modell für ein Target in Docker. Die Anfrage wird zuerst gegen die Capabilities des
Targets geprüft: Eine nicht unterstützte Kombination stoppt mit Exit-Code 2 und dem Grund, bevor etwas läuft.
Fehlendes Board und fehlende Quantisierung werden mit den Standardwerten des Targets ergänzt.

| Option | Bedeutung |
|---|---|
| `-m, --model TEXT` | **Pflicht.** Hugging-Face-ID (z. B. `HuggingFaceTB/SmolLM2-135M-Instruct`) oder ein lokaler Ordner bzw. eine Datei. |
| `-t, --target TEXT` | **Pflicht.** Ordnername des Targets in `targets/`, z. B. `Rockchip`. Groß- und Kleinschreibung spielt keine Rolle. Ein unbekanntes Target stoppt mit Exit-Code 2 und der Liste der bekannten Targets. |
| `-f, --format [gguf\|rkllm\|onnx\|tflite\|pytorch_mobile\|rknn\|tensorrt\|openvino\|coreml\|ncnn\|mnn]` | Ausgabeformat (Standard `gguf`). Die Liste ist länger als das, was ein Target unterstützt; Rockchip nimmt `gguf`, `rkllm` und `rknn`. |
| `-b, --board TEXT` | Board der Target-Familie, z. B. `rk3588`, `rk3576`, `rk3566`. Standard: das Standard-Board des Targets (Rockchip: `rk3588`). Groß- und Kleinschreibung spielt keine Rolle. |
| `-q, --quantization TEXT` | Quantisierung, z. B. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Standard: der Standard des Formats für das Board. Gängige Schreibweisen wie `INT8` werden übersetzt. |
| `-o, --output-dir TEXT` | Ausgabeordner, relativ zum aktuellen Ordner. Standard: `output/build_<Datum>_<Uhrzeit>` in der Installation. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimierungsstufe (Standard `balanced`), wird den Build-Skripten als `OPTIMIZATION_LEVEL` übergeben. Die Rockchip-Skripte werten sie nicht aus. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in der Build-Warteschlange dieses Prozesses (Standard `normal`). |
| `--parallel` | Hat in dieser Version keine Wirkung (es ist immer an, und ein Aufruf startet einen Build). |
| `-F, --follow` | Jede Stufe ausgeben, während der Build läuft. |
| `--gpu / --no-gpu` | Die GPU des Hosts in den Container durchreichen (Standard aus): eine NVIDIA-GPU, wenn `nvidia-smi` gefunden wird, sonst `/dev/dri`, falls vorhanden. Rockchip-Konvertierungen brauchen das nicht. |
| `--imatrix / --no-imatrix` | Importance Matrix für GGUF (Standard aus). Pflicht für `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`. |
| `--dataset PATH` | Deine eigene Kalibrierungsdatei (muss existieren): eine Textdatei für die GGUF-Importance-Matrix oder für RKLLM, eine Liste mit Beispieleingaben für RKNN `i8`. Ohne sie nutzt die Importance Matrix den mitgelieferten Kalibrierungstext. |

Verhalten:

- Der Befehl bleibt im Vordergrund, bis der Build fertig ist. Strg+C bricht den Build in dieser Version nicht ab:
  Die CLI zeigt keinen Fortschritt mehr, wartet aber, bis der Build zu Ende ist, und endet dann mit Exit-Code 130.
- Scheitert der Build an einem bekannten Problem mit einer Korrektur, die das Framework selbst anwenden kann, zeigt
  die CLI die Ursache und fragt „Apply this fix and retry the build?“. Ohne Terminal wird der Vorschlag verworfen.
  Manuelle Schritte werden nur angezeigt.
- Ergebnisse: `<Ausgabeordner>/<Target>/<Modell>/<Quantisierung>/` mit der Modelldatei, `build_info.json` und
  `Model_Card.md`, dazu ein ZIP davon neben diesem Ordner. Sie erscheinen dort erst, wenn der Build abgeschlossen
  ist; ein abgebrochener oder gescheiterter Build hinterlässt dort keine Dateien.
- Zugangsbeschränkte Hugging-Face-Modelle brauchen ein Token: `llm-cli secrets set hf_token`.
- Der erste Build eines Targets baut zuerst sein Docker-Image und dauert deutlich länger.

```bash
# GGUF für die CPU des RK3566 (Standard-Quantisierung Q4_0 auf diesem Board)
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3566 --follow

# RKLLM für die NPU des RK3576 (Standard w4a16)
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3576 -F

# 2-Bit-GGUF: braucht die Importance Matrix
llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f gguf -b rk3588 -q IQ2_XS --imatrix -F

# RKNN int8 aus einem lokalen ONNX-Modell mit Beispielliste
llm-cli build start -m ./detector.onnx -t Rockchip -f rknn -b rk3588 -q i8 --dataset ./samples.txt -F

# Ergebnisse in einen Ordner deiner Wahl
llm-cli build start -m ./my-model -t Rockchip -o ./builds -F
```

So sieht eine abgelehnte Anfrage aus:

```
$ llm-cli -q build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t Rockchip -f rkllm -b rk3566
Format 'rkllm' does not run on rk3566 (supported: rk3588, rk3576).
$ echo $?
2
```

### build status

```
llm-cli build status [REQUEST_ID] [-a|--all]
```

Zeigt die Builds, die dieser Prozess kennt: ohne Request-ID (oder mit `--all`) eine Tabelle mit Request-ID, Status
und Fortschritt; mit Request-ID deren Status und Fortschritt.

| Option | Bedeutung |
|---|---|
| `-a, --all` | Alle Builds zeigen. |

Builds leben in dem Prozess, der sie gestartet hat. Ein eigener Aufruf von `llm-cli build status` sieht keine Builds
aus der GUI, dem MCP-Server oder einem anderen CLI-Aufruf und meldet „No builds found“. Nutze `build start --follow`,
das Log in der GUI oder `llm-cli deploy list-artifacts` für fertige Builds.

---

## deploy

### deploy list-artifacts

```
llm-cli deploy list-artifacts
```

Listet die fertigen Builds im eingestellten Ausgabeordner auf, gelesen aus ihren Manifesten: Ordner (relativ zum
Ausgabeordner), Modell, Format, Quantisierung, Board und Größe.

### deploy package

```
llm-cli deploy package ARTIFACT_NAME --profile PROFIL [--docker|--no-docker]
```

Erstellt eine ZIP-Datei für das Board: die Modelldatei, Manifest und Model Card des Builds, ein erzeugtes
`deploy.sh` und `checksums.sha256`. Sie wird als `deploy_<Modell>_<Zeitstempel>.zip` in den Ausgabeordner
geschrieben.

| Argument / Option | Bedeutung |
|---|---|
| `ARTIFACT_NAME` | Ein Build-Ordner, wie ihn `list-artifacts` zeigt (relativ zum Ausgabeordner), oder ein beliebiger vorhandener Pfad zu einem Build-Ordner oder einer Modelldatei. |
| `--profile TEXT` | **Pflicht.** Das Target, z. B. `Rockchip` (für Geräteoptionen im Docker-Modus). |
| `--docker / --no-docker` | Zusätzlich Container-Images einpacken (Standard aus). Experimentell und in dieser Version nicht geprüft; für eine Modelldatei aus lassen. |

Ohne Docker werden nur Modelldateien übertragen; Builds mit mehreren Gewichtsdateien werden abgelehnt.

```bash
llm-cli deploy package build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
```

### deploy run

```
llm-cli deploy run PACKAGE_PATH --ip IP --user BENUTZER [--port 22] [--password TEXT] [--trust-fingerprint TEXT]
```

Lädt ein Paket per SSH in einen privaten temporären Ordner auf dem Board, führt dort `deploy.sh` aus und löscht den
Ordner danach. `deploy.sh` prüft die Prüfsummen und kopiert das Modell nach `~/models` (oder `$MODEL_DIR`); für GGUF
gibt es eine passende `llama-server`-Befehlszeile aus, wenn `llama-server` auf dem Board installiert ist.

| Argument / Option | Bedeutung |
|---|---|
| `PACKAGE_PATH` | Die ZIP-Datei aus `deploy package`, das ihren vollständigen Pfad ausgibt (muss existieren). |
| `--ip TEXT` | **Pflicht.** Die IP-Adresse des Boards. |
| `--user TEXT` | **Pflicht.** Benutzer auf dem Board. |
| `--port INTEGER` | SSH-Port (Standard 22). |
| `--password TEXT` | Nur für die erste Verbindung: wird einmal genutzt, um den SSH-Schlüssel des Frameworks zu hinterlegen, und nie gespeichert. Ohne Angabe wird interaktiv gefragt. Ein Passwort auf der Befehlszeile landet in deinem Shell-Verlauf. |
| `--trust-fingerprint TEXT` | Einem neuen Board ohne Nachfrage vertrauen, wenn sein Host-Schlüssel genau diesen SHA256-Fingerabdruck hat (für Skripte). |

Erste Verbindung: Die CLI zeigt den Fingerabdruck des Host-Schlüssels und fragt, ob du ihm vertraust. Vergleiche ihn
mit `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` auf dem Board. Ohne Terminal und ohne `--trust-fingerprint`
wird nichts übertragen. Ein Board, dessen Host-Schlüssel sich geändert hat, wird immer abgelehnt.

```bash
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --ip 192.168.1.50 --user rock

# In einem Skript, mit vorher geprüftem Fingerabdruck
llm-cli deploy run output/deploy_model_1791801300.zip --ip 192.168.1.50 --user rock \
  --trust-fingerprint SHA256:AbCdEf...
```

---

## ditto

Ditto ist der eingebaute Assistent. Die meisten `ditto`-Befehle nutzen Anbieter und Modell aus den KI-Einstellungen
(GUI: **Tools > Configure AI Agent (Ditto)...**, oder `ai_provider` und `ai_model` in der `config.yml`).
API-Schlüssel speicherst du mit [`llm-cli secrets set`](#secrets).

### ditto setup

```
llm-cli ditto setup
```

Ersteinrichtung der Tool-Berechtigungen, die Ditto und der MCP-Server verwenden. Stellt eine Frage pro Kategorie:

| Kategorie | Auswahl | Empfohlen |
|---|---|---|
| Informationen lesen (Targets, Builds, Logs, Wissensbasis) | allow, ask, deny | allow |
| Auf diesem Rechner etwas ändern (Builds starten oder abbrechen) | allow, ask, deny | ask |
| Andere Geräte erreichen oder Daten löschen (Übertragen per SSH, Uploads) | ask, deny | ask |

Die Antworten werden in `~/.llm-framework/tool_policy.json` gespeichert. Später änderst du sie mit
[`llm-cli tools permissions`](#tools-permissions).

### ditto ask

```
llm-cli ditto ask FRAGE [--no-tools] [--provider TEXT] [--model TEXT]
```

Stellt Ditto eine Frage. Ditto darf die Tools des Frameworks im Rahmen deiner Berechtigungen nutzen; Tools, die auf
„ask“ stehen, zeigen das Tool und jedes Argument und brauchen deine Bestätigung im Terminal (ohne Terminal werden sie
abgelehnt). Die Antwort geht nach stdout, Tool-Hinweise und Fehler nach stderr. Nichts wird im Chatverlauf
gespeichert.

| Option | Bedeutung |
|---|---|
| `--no-tools` | Ohne die Tools des Frameworks antworten. |
| `--provider TEXT` | Anbieter nur für diese Frage, z. B. `anthropic`, `openai`, `ollama`, `local`. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell nur für diese Frage. Standard: KI-Einstellungen. |

```bash
llm-cli ditto ask "Welche Quantisierung nehme ich für ein 1B-Modell auf dem RK3566?"
llm-cli ditto ask "Starte einen GGUF-Build von HuggingFaceTB/SmolLM2-135M-Instruct für rk3588"
llm-cli ditto ask "Erkläre IQ4_XS" --no-tools --provider local
```

Ohne Einrichtung erklärt der Befehl, was fehlt, zum Beispiel „the local model server is not running. Start it with:
llm-cli ditto runtime start“.

### ditto eval

```
llm-cli ditto eval [Optionen]
```

Bewertet Ditto anhand fester Testfälle (Analyse von Build-Logs, Build-Empfehlungen, Modulvorschläge, Chat mit Tools,
Prompt-Injection), zum Beispiel um zu prüfen, ob ein lokales 2-Bit-Modell gut genug ist. Nichts wird gebaut oder
geändert: Chat-Fälle laufen gegen einen Ersatz-Build-Dienst. Cloud-Anbieter berechnen jede Anfrage (etwa 20 pro
Lauf und Wiederholung). Wie es funktioniert: [Features_DE.md](Features_DE.md#ditto-evaluation).

| Option | Bedeutung |
|---|---|
| `--cases FILE` | Datei mit den Fällen (Standard: `evals/ditto/cases.yml` der Installation). |
| `--provider TEXT` | Anbieter für den Lauf. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell für den Lauf. Standard: KI-Einstellungen. |
| `--tier TEXT` | Gegen das verwaltete lokale Modell dieser Stufe laufen; wiederholbar (`--tier small --tier small-q2`). Nicht mit `--provider`/`--model` kombinierbar. |
| `--kind [healing\|optimizer\|module\|chat]` | Nur Fälle dieser Art; wiederholbar. |
| `--case TEXT` | Nur diese Fall-ID; wiederholbar. |
| `--repeat INTEGER RANGE` | Läufe pro Fall, 1 bis 20 (Standard 1). |
| `--json FILE` | Den vollständigen Bericht (Einrichtung, Bestehensquoten, jede Antwort) als JSON schreiben. |
| `--min-pass FLOAT RANGE` | Exit-Code 1, wenn ein Lauf weniger Fälle besteht als dieser Anteil (0 bis 1, z. B. `0.8`). |
| `--effort [low\|medium\|high]` | Aufwand für diesen Lauf (lokale Modelle: `low` = ohne Nachdenken). Standard: KI-Einstellungen. |

Mit `--tier` muss die lokale Laufzeit installiert und das Modell der Stufe heruntergeladen sein. Die CLI startet
`llama-server` nacheinander mit jeder Stufe und **stoppt den lokalen Server nach jeder Stufe**; ein Server, der
vorher lief, wird also ebenfalls gestoppt.

```bash
# Das 2B-Modell mit 4 und 2 Bit auf diesem Rechner vergleichen, ohne Nachdenken
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json

# Nur die Chat-Fälle, je drei Läufe, gegen den eingestellten Anbieter
llm-cli ditto eval --kind chat --repeat 3

# Als Qualitätsschwelle in der CI
llm-cli ditto eval --provider anthropic --min-pass 0.8
```

Das Ergebnis ist eine Tabelle pro Lauf: bestandener Anteil, Anteil pro Art und durchschnittliche Sekunden pro Fall.

### ditto runtime

Der verwaltete llama.cpp-Server für Dittos Offline-Modus. Diese Befehle funktionieren ohne Docker und ohne
Schlüsselbund. Dateien: `~/.llm-framework/runtime/llama.cpp/` (Builds und Zustand) und
`~/.llm-framework/logs/llama-server.log`.

#### ditto runtime status

```
llm-cli ditto runtime status [--json]
```

Zeigt die erkannte Hardware, den empfohlenen llama.cpp-Build und warum, ob ein Build installiert ist, ob der Server
läuft (Adresse, Prozess, Modell, Kontext), das empfohlene Modell und warum, und die Log-Datei.

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe. |

#### ditto runtime install

```
llm-cli ditto runtime install [--backend auto|cpu|vulkan|cuda|rocm|metal] [--force] [-y]
```

Lädt den festgelegten llama.cpp-Build für diesen Rechner herunter, prüft Größe und sha256 vor dem Entpacken und
installiert ihn.

| Option | Bedeutung |
|---|---|
| `--backend [auto\|cpu\|vulkan\|cuda\|rocm\|metal]` | Zu installierender Build (Standard `auto`). ROCm wird nur genutzt, wenn er hier gewählt wird. |
| `--force` | Erneut herunterladen und entpacken, auch wenn er installiert ist. |
| `-y, --yes` | Vor dem Download nicht fragen. |

#### ditto runtime start

```
llm-cli ditto runtime start [--model STUFE] [--ctx N] [--sleep-idle SEKUNDEN]
```

Startet `llama-server` auf 127.0.0.1 mit einem freien Port und einem neuen API-Schlüssel in einer privaten Datei. Der
Server läuft weiter, nachdem der Befehl zurückkehrt, bis `llm-cli ditto runtime stop`. Läuft dasselbe Modell schon,
wird es weiterverwendet.

| Option | Bedeutung |
|---|---|
| `--model TEXT` | Modellstufe (siehe `ditto models list`); wird für spätere Starts gemerkt. `auto`: die Empfehlung für diesen Rechner. |
| `--ctx INTEGER RANGE` | Kontextlänge in Tokens, 256 bis 1048576. Standard: aus dem freien Speicher berechnet, höchstens 32768. |
| `--sleep-idle INTEGER RANGE` | Das Modell nach so vielen Sekunden ohne Anfrage entladen (0 = nie). Standard: 600. |

Das Modell muss vorher heruntergeladen sein (`ditto models download`). Ditto nutzt den Server als Anbieter `local`.

#### ditto runtime stop

```
llm-cli ditto runtime stop
```

Stoppt den vom Framework gestarteten Server, auch Reste nach einem Absturz. Meldet „Not running.“, wenn keiner läuft.

```bash
llm-cli ditto runtime status
llm-cli ditto runtime install -y
llm-cli ditto models download -y
llm-cli ditto runtime start --model small --sleep-idle 0
llm-cli ditto runtime stop
```

### ditto models

#### ditto models list

```
llm-cli ditto models list
```

Listet die festgelegten Modelle auf: Stufe, Datei, Größe, Lizenz, ob es heruntergeladen (und geprüft) ist, und
Hinweise („recommended“, „chosen“, „on request“).

#### ditto models download

```
llm-cli ditto models download [STUFE] [-y]
```

Lädt ein Modell in seiner festgelegten Hugging-Face-Revision herunter und prüft Größe und sha256. Ohne `STUFE` die
empfohlene Stufe für diesen Rechner. Unterbrochene Downloads werden fortgesetzt.

| Argument / Option | Bedeutung |
|---|---|
| `STUFE` | `small`, `small-q2`, `default`, `default-q2`, `large`, `large-iq2` oder `granite`. |
| `-y, --yes` | Vor dem Download nicht fragen. |

```bash
llm-cli ditto models list
llm-cli ditto models download small-q2
```

---

## tools

Die Tools des Frameworks sind das, was Ditto und MCP-Clients verwenden: 8 nur lesende Tools (`list_targets`,
`get_target_capabilities`, `list_build_requests`, `get_build_status`, `read_build_log`, `list_artifacts`,
`run_doctor`, `search_knowledge`) und 3, die auf diesem Rechner etwas ändern (`start_build`, `cancel_build`,
`resolve_healing`).

### tools list

```
llm-cli tools list
```

Jedes Tool mit seiner Stufe (`read`, `local_write`), der aktuellen Berechtigung (`allow`, `ask`, `deny`) und einer
Beschreibung. Der Titel sagt, wenn die Berechtigungen noch nicht eingerichtet sind.

### tools permissions

```
llm-cli tools permissions [--reset]
```

Stellt dieselben drei Fragen wie [`ditto setup`](#ditto-setup) und speichert die Antworten. Berechtigungen für
einzelne Tools, die in der GUI gesetzt wurden, bleiben erhalten.

| Option | Bedeutung |
|---|---|
| `--reset` | Zurück zu den empfohlenen Standardwerten (die Einrichtung fragt beim nächsten Mal erneut). |

### tools audit

```
llm-cli tools audit [--limit N] [--verify] [--archive]
```

Zeigt die letzten Tool-Aufrufe: Zeit, Akteur (`ditto` oder `mcp`), Tool, Entscheidung, ob ein Mensch bestätigt hat,
und das Ergebnis.

| Option | Bedeutung |
|---|---|
| `--limit INTEGER` | Anzahl der Einträge (Standard 30). |
| `--verify` | Stattdessen die Kette prüfen: geänderte oder entfernte Einträge und das Entfernen der neuesten Einträge (Anker-Datei). Exit-Code 0, wenn intakt, sonst 1. |
| `--archive` | Das Log und seinen Anker beiseitelegen (aufbewahrt als `audit.db.<UTC-Zeit>` und `audit.db.<UTC-Zeit>.anchor` im persönlichen Ordner) und mit dem nächsten Tool-Aufruf ein neues Log beginnen. Nutze es, wenn `--verify` oder `llm-cli doctor` ein Problem mit dem Log meldet. Lässt sich das Log nicht beiseitelegen (zum Beispiel, weil es gerade benutzt wird), endet der Befehl mit Exit-Code 1; schließe die GUI und die KI-Clients und versuche es erneut. |

```bash
llm-cli tools audit --limit 100
llm-cli tools audit --verify
llm-cli tools audit --archive
```

---

## mcp

### mcp serve

```
llm-cli mcp serve [--transport stdio|http] [--host HOST] [--port PORT]
```

Betreibt den MCP-Server. Normalerweise startet ihn der KI-Client für dich (siehe `mcp config`). stdout trägt das
Protokoll, Logs gehen nach stderr. Sind die Tool-Berechtigungen noch nicht eingerichtet, funktionieren nur lesende
Tools ohne Nachfrage, alles andere fragt (oder wird abgelehnt, wenn der Client nicht fragen kann).

| Option | Bedeutung |
|---|---|
| `--transport [stdio\|http]` | `stdio` (Standard): vom Client gestartet. `http`: lokales Streamable HTTP mit Bearer-Token. |
| `--host TEXT` | Nur HTTP; nur Loopback-Adressen (Standard `127.0.0.1`; außerdem `::1`, `localhost`). |
| `--port INTEGER` | Nur HTTP (Standard 8765). |

Mit `--transport http` lauscht der Server auf `http://<Host>:<Port>/mcp`, und jede Anfrage braucht
`Authorization: Bearer <Token>`; das Token steht in `~/.llm-framework/mcp_http_token` (wird beim ersten Mal
angelegt).

```bash
llm-cli mcp serve --transport http --port 8765
```

### mcp config

```
llm-cli mcp config [--write claude-desktop]
```

Zeigt, wie du Claude Code (ein Befehl `claude mcp add ...`), Claude Desktop (JSON für dessen Konfigurationsdatei) und
VS Code (JSON für `.vscode/mcp.json`) verbindest, jeweils mit dem Python und dem Ordner dieser Installation. Ruf es
mit dem Python des Frameworks auf, damit der ausgegebene Befehl dieses verwendet. Der Befehl startet Python mit `-P`,
sodass nichts aus dem Ordner importiert wird, in dem der Client den Server startet. Unter Windows ist die
Befehlszeile so gequotet, dass cmd und PowerShell sie unverändert lassen; enthält ein Pfad `%`, `!`, `$`, einen
Backtick oder ein doppeltes Anführungszeichen, wird nur das JSON gezeigt.

| Option | Bedeutung |
|---|---|
| `--write [claude-desktop]` | Den Server in die Konfiguration von Claude Desktop eintragen (der Pfad steht in der Ausgabe; unter Linux wird `XDG_CONFIG_HOME` beachtet). Andere Einträge bleiben erhalten, und eine Sicherung (`.bak`) wird geschrieben. Lässt sich die Datei nicht schreiben, nennt der Befehl den Grund und endet mit Exit-Code 1. Danach Claude Desktop neu starten. |

```bash
llm-cli mcp config
llm-cli mcp config --write claude-desktop
```

An diesen Clients meldest du dich mit deinem eigenen Konto oder Schlüssel an; das Framework sieht das nie.

---

## secrets

API-Schlüssel und Tokens werden verschlüsselt im Tresor für Zugangsdaten `secrets.store` in deinem persönlichen
Statusordner gespeichert (`~/.llm-framework` oder `LLM_FRAMEWORK_HOME`). Der Tresor liegt nie in einer
Git-Arbeitskopie. Sein Schlüssel liegt standardmäßig im Schlüsselbund deines Betriebssystems. Ohne nutzbaren
Schlüsselbund brechen Befehle, die das Framework brauchen, mit Exit-Code 1 ab und nennen die Möglichkeiten; einen
anderen Schutz wählst du mit der Einstellung `secret_storage` in `config.yml` oder für einen einzelnen Lauf mit der
Umgebungsvariable `LLM_SECRET_STORAGE`:

| `secret_storage` | Woher der Schlüssel des Tresors kommt |
|---|---|
| `keyring` | Aus dem Schlüsselbund des Betriebssystems (Standard). |
| `helper` | Vom Befehl in der Einstellung `secret_helper`, z. B. `pass show llmcc/vault-key`, ohne Shell ausgeführt. |
| `passphrase` | Aus einer Passphrase, die einmal je Start abgefragt wird (mindestens 8 Zeichen), nie aus einer Datei oder Variable. |
| `insecure` | Aus einer Schlüsseldatei neben dem Tresor: **im Ruhezustand nicht verschlüsselt**. |
| `ephemeral` | Nur im Arbeitsspeicher, nichts wird gespeichert (für CI). |

### secrets set

```
llm-cli secrets set KEY [--password TEXT]
```

Speichert ein Geheimnis unter dem Namen `KEY`. Ohne `--password` wird der Wert zweimal ohne Anzeige abgefragt; so
bleibt er aus deinem Shell-Verlauf.

| Option | Bedeutung |
|---|---|
| `--password TEXT` | Der Wert (besser vermeiden: er landet in deinem Shell-Verlauf). |

Namen, die das Framework verwendet:

| Name | Wofür |
|---|---|
| `anthropic_api_key`, `openai_api_key`, `gemini_api_key`, `openrouter_api_key`, `custom_api_key` | Dittos Anbieter |
| `hf_token` | Zugangsbeschränkte Hugging-Face-Modelle in Builds |
| `github_token` | `swarm upload` |
| `qdrant_api_key` | Ein entfernter Qdrant-Server für die Wissensbasis (Einstellung `qdrant_url`) |

```bash
llm-cli secrets set anthropic_api_key
llm-cli secrets set hf_token
```

### secrets list

```
llm-cli secrets list
```

Listet die gespeicherten Namen auf, nie die Werte. Die Schlüssel für Dittos Anbieter erscheinen unter ihren
Namen im Tresor, z. B. `cred/anthropic/api_key` für `anthropic_api_key`; `secrets set` nimmt beide Formen an.

---

## module

Neue Hardware-Targets. Die vollständige Anleitung: [Module_Developer_Guide_DE.md](Module_Developer_Guide_DE.md).

### module create

```
llm-cli module create [--name TEXT] [--arch aarch64|x86_64|armv7l|riscv64] [--sdk TEXT]
```

Legt ein neues Target per Fragen an: Name, Architektur und SDK (wenn nicht als Option angegeben), dann
Docker-Basis-Image (Standard `debian:bookworm-slim`), Beschreibung, Systempakete und Standard-CPU-Flags. Jeder Wert
wird geprüft, bevor Dateien geschrieben werden; ein vorhandenes Target wird nie überschrieben.

| Option | Bedeutung |
|---|---|
| `--name TEXT` | Name des neuen Targets. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU-Architektur. |
| `--sdk TEXT` | Besonderes SDK, z. B. `CUDA`, `RKNN` (Standard `None`). |

Das Target wird nach `targets/` **im aktuellen Ordner** geschrieben; ruf den Befehl im Installationsordner auf.
Danach trägst du Boards in `target.yml` ein und passt `modules/config_module.sh` an.

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto schlägt ein Target-Modul aus einer Hardware-Probe-Datei vor (`target_hardware_config.txt`, siehe
[scan](#scan)). Der ganze Vorschlag wird als JSON ausgegeben; erst nach deiner Bestätigung wird das Modul im
eingestellten Targets-Ordner angelegt (und vom Modulgenerator erneut geprüft).

| Option | Bedeutung |
|---|---|
| `--provider TEXT` | Anbieter für diesen Lauf, z. B. `anthropic`, `openai`, `ollama`. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell für diesen Lauf. Standard: KI-Einstellungen. |

```bash
llm-cli scan
llm-cli module generate-ai target_hardware_config.txt --provider local
```

---

## scan

```
llm-cli scan [--output TEXT]
```

Führt die Hardware-Probe auf diesem Rechner aus (`scripts/hardware_probe.sh` unter Linux,
`scripts/hardware_probe.ps1` unter Windows). Die Probe schreibt `target_hardware_config.txt` in den aktuellen
Ordner. Sie fragt eventuell, ob fehlende Hilfsprogramme (`pciutils`, `usbutils`, `dmidecode`) mit deinem
Paketmanager installiert werden sollen; mit `n` überspringst du das.

| Option | Bedeutung |
|---|---|
| `--output TEXT` | Dateiname, nach dem der Befehl danach sucht (Standard `target_hardware_config.txt`). Die Probe selbst schreibt immer `target_hardware_config.txt`; lass also den Standard stehen. |

Um ein Board statt dieses Rechners zu untersuchen, führst du `scripts/hardware_probe.sh` auf dem Board aus oder
nutzt in der GUI **Scan / Import... > Remote Scan via SSH**.

---

## config

### config show

```
llm-cli config show
```

Gibt die Grundeinstellungen der CLI als JSON aus: die Ordner (`targets_dir`, `models_dir`, `output_dir`,
`configs_dir`, `cache_dir`, `logs_dir`), `log_level` und `max_concurrent_builds`, ergänzt um eine mit `-c`
angegebene Datei. Die Einstellungen in der `config.yml` (KI-Anbieter, Sandbox, Crawler und so weiter) zeigt es
nicht; sieh dafür direkt in diese Datei.

### config repos

```
llm-cli config repos [--add NAME URL] [--remove NAME]
```

Ohne Optionen listet es die Überschreibungen für Quell-Repositorys auf (Einstellung `source_repositories`). `--add`
und `--remove` ändern sie und speichern die `config.yml`.

| Option | Bedeutung |
|---|---|
| `--add TEXT...` | Eine Überschreibung hinzufügen: `NAME URL`. |
| `--remove TEXT` | Die Überschreibung `NAME` entfernen. |

In dieser Version erreichen Überschreibungen, die hier hinzugefügt werden, die Builds nicht: Die Build-Engine reicht
nur Einträge der Form `{url: ...}` weiter, und `--add` speichert eine einfache URL.

---

## swarm

### swarm upload

```
llm-cli swarm upload FILE_PATH [--user TEXT]
```

Lädt einen Export der Wissensbasis (eine JSON-Datei) in das Community-Repository auf GitHub hoch. Die Datei wird
verschleiert (XOR), **nicht verschlüsselt**: Jeder kann sie lesen. Lade nur hoch, was du auch veröffentlichen würdest.

| Argument / Option | Bedeutung |
|---|---|
| `FILE_PATH` | Die Export-Datei (muss existieren). |
| `--user TEXT` | Name des Beitragenden (Standard `CLI_User`). |

Braucht ein GitHub-Token: `llm-cli secrets set github_token` (der Wert wird abgefragt).

---

## Bekannte Einschränkungen

- `build status` sieht nur Builds des eigenen Prozesses (siehe [build status](#build-status)).
- Strg+C in `build start` bricht den Build nicht ab; die CLI wartet, bis er zu Ende ist.
- `build start --parallel` und `--optimization` wirken sich auf Rockchip-Builds nicht aus.
- `deploy package`, `secrets set` und `scan` melden einen Fehler („Package generation failed“, „Failed to store
  secret“, „output file was not created“), enden aber mit Exit-Code 0; prüfe in Skripten die Ausgabe.
- `scan --output` ändert nicht, wohin die Probe schreibt.
- `module create` schreibt nach `targets/` im aktuellen Ordner.
- `config show` zeigt nur die Grundeinstellungen der CLI; `config repos --add` erreicht die Builds nicht.
- `deploy package --docker` ist experimentell.
- Einen CLI-Befehl, um Dittos Anbieter dauerhaft zu wählen, gibt es noch nicht; nutze die GUI, die `config.yml` oder
  `LLM_FRAMEWORK_AI_PROVIDER`. Die Anmeldung pro Anbieter wird gerade gebaut ([Roadmap](BLUEPRINT_DE.md#roadmap)).
