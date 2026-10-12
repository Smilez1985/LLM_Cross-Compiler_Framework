Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md) · Zuletzt aktualisiert am 2026-10-11

[English](CLI_Usage.md) · **Deutsch**

# Kommandozeile (`llm-cli`)

Die Kommandozeile kann alles, was du auf einem Server ohne Bildschirm, in Skripten und in der CI brauchst: die
Installation prüfen, Modelle konvertieren, sie übertragen, Ditto und sein lokales Modell einrichten,
Tool-Berechtigungen verwalten, den MCP-Server betreiben und Community-Pakete teilen oder installieren. Jeder Befehl
und jede Option unten stammt aus der echten `--help`-Ausgabe dieser Version.

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
- [ditto](#ditto): `login`, `logout`, `auth`, `setup`, `ask`, `eval`, `runtime`, `models`
- [tools](#tools): `list`, `permissions`, `audit`
- [mcp](#mcp): `serve`, `config`, `token add|list|rotate|revoke`
- [secrets](#secrets): `set`, `list`
- [module](#module): `create`, `generate-ai`
- [scan](#scan): Hardware-Probe
- [device](#device): `list`, `settings`, `runtime`
- [config](#config): `show`, `repos`
- [zoo](#zoo): `list`, `show`, `install`, `verify`, `export`, `keys`
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
| `LLM_FRAMEWORK_HOME` | Ordner für den persönlichen Zustand. Standard: `~/.llm-framework`. Darfst du im Installationsordner nicht schreiben (zum Beispiel `C:\Program Files`), liegen auch die Datenordner `output/`, `cache/` und `models/` dort. |
| `LLM_FRAMEWORK_MODELS_DIR` | Modellordner; Dittos lokale Modelle liegen im Unterordner `ditto/`. Standard: `models/` in der Installation, wenn beschreibbar, sonst im persönlichen Ordner. |
| `LLM_FRAMEWORK_<SCHLÜSSEL>` | Überschreibt eine Einstellung für diesen Aufruf, z. B. `LLM_FRAMEWORK_AI_PROVIDER=local` oder `LLM_FRAMEWORK_BUILD_MEMORY_LIMIT=24g`. Es zählen nur bekannte Einstellungsnamen. |
| `LLM_SECRET_STORAGE` | Wie der Secrets-Tresor für diesen Aufruf geschützt wird, z. B. `ephemeral` in der CI (siehe [secrets](#secrets)). |
| `OPENAI_CUSTOM_HEADERS`, `ANTHROPIC_CUSTOM_HEADERS` | Werden beim Start mit einer Warnung entfernt, damit sie den gespeicherten Schlüssel nicht ersetzen können; die Einstellung `ai_allow_sdk_env_headers: true` behält sie. |
| `ANTHROPIC_LOG`, `OPENAI_LOG` | `debug` wird auf `info` gesenkt, außer `--debug-http` ist angegeben. |

---

## Globale Optionen, Ausgabe und Exit-Codes

```
llm-cli [OPTIONEN] BEFEHL [ARGUMENTE]...
```

| Option | Bedeutung |
|---|---|
| `--version` | Version ausgeben und beenden. |
| `-c, --config PATH` | Diese Einstellungsdatei verwenden (YAML oder JSON, muss existieren). Ihr Ordner wird für diesen Aufruf zum Einstellungsordner (eine `config.yml` dort wird ebenfalls gelesen), und die Werte der Datei haben Vorrang davor. Der übliche Einstellungsordner wird dann nicht gelesen. Eine Datei, die sich nicht lesen lässt, wird auf stderr genannt und übergangen; die übrigen Einstellungen (jene `config.yml`, `LLM_FRAMEWORK_*`-Variablen, Standardwerte) gelten weiter. |
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
nach stdout, die Befehle zur Anmeldung (`ditto login`, `logout`, `auth`) geben Fehler, Warnungen und Fragen auf
stderr aus, und bei `llm-cli mcp serve` trägt stdout das MCP-Protokoll.

**Exit-Codes:**

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Der Befehl ist gescheitert (zum Beispiel ein gescheiterter Build, eine gescheiterte Übertragung, ein Paket, das sich nicht erstellen ließ, ein Geheimnis, das sich nicht speichern ließ, eine Hardware-Probe, die keine Datei geschrieben hat, ein beschädigtes Audit-Log, ein KI-Fehler) |
| 2 | Ungültige Eingabe: unbekannte Option, ein unbekanntes Target oder eine nicht unterstützte Kombination aus Format, Board und Quantisierung (`build start`), ein unbekannter Anbieter (`--provider`), eine unbekannte Modellstufe (`ditto models download`), kein passender Evaluationsfall (`ditto eval`), eine fehlende Paket-ID, Datei oder Quelle (`zoo install`, `zoo export`), eine Schlüsseldatei ohne verwendbaren Schlüssel oder keine Textdatei (`--to`, `--from`), `--from` für ein Paket, das nicht verschlüsselt ist (`zoo install`), `--to` zusammen mit `--passphrase` (`zoo export`) |
| 3 | Anmeldung: Der Anbieter hat die Zugangsdaten abgelehnt (`ditto login`, `ditto auth status --check`) |
| 4 | Anmeldung: Anbieter oder lokaler Server nicht erreichbar |
| 5 | Der Tresor für Zugangsdaten lässt sich nicht nutzen (kein nutzbarer Schlüsselbund, keine Passphrase, ein beschädigter Tresor); die Meldung nennt die Möglichkeiten. Jeder Befehl, der den Tresor braucht, verwendet ihn |
| 6 | Anmeldung: Das Verfahren ist in dieser Version nicht verfügbar, oder der Anbieter wurde entfernt |
| 130 | `build start` wurde mit Strg+C unterbrochen (der Build wurde vorher abgebrochen) |

`llm-cli doctor` verwendet 0 (OK), 1 (Warnungen) und 2 (Fehler); `llm-cli ditto eval --min-pass` liefert 1, wenn
ein Lauf unter dem angegebenen Anteil bleibt.

---

## Überblick über alle Befehle

| Befehl | Was er tut |
|---|---|
| `doctor` | Die Installation prüfen und erklären, wie man Probleme behebt |
| `targets list` / `targets validate` | Hardware-Targets mit ihren Boards und Formaten auflisten / prüfen, ob ein Target lädt und seine Capabilities gültig sind |
| `build start` / `build status` | Ein Modell konvertieren und quantisieren / Builds zeigen, auch solche, die die GUI, der MCP-Server oder ein anderer CLI-Aufruf gestartet hat |
| `deploy list-artifacts` / `deploy package` / `deploy run` | Fertige Builds auflisten / einen Build in eine ZIP-Datei packen / sie auf ein Board laden und installieren |
| `ditto login` / `ditto logout` | Ditto bei einem Anbieter anmelden (der Schlüssel verdeckt abgefragt, von stdin oder aus einer Umgebungsvariable, vor dem Speichern geprüft) / abmelden |
| `ditto auth status/methods/storage/rotate-key` | Anmeldestand aller Anbieter / die Anmeldeverfahren / wie der Tresor geschützt ist / ein neuer Schlüssel für den Tresor |
| `ditto setup` | Die Tool-Berechtigungen festlegen (dieselbe Auswahl wie im Dialog beim ersten Start der GUI) |
| `ditto ask` | Ditto eine Frage stellen |
| `ditto eval` | Ditto anhand fester Testfälle bewerten, oder die Suche der Wissensbasis |
| `ditto runtime install/start/status/stop/import/export/prune` | Der verwaltete lokale Modellserver (llama.cpp, auf diesem Rechner gebaut) für den Offline-Modus |
| `ditto runtime export-source/import-source` | Den festgelegten llama.cpp-Quelltext als Git-Bundle auf einen Rechner ohne Netz tragen (am Commit geprüft) |
| `ditto models list/download` | Die Modellstufen für den Offline-Modus |
| `tools list` / `tools permissions` / `tools audit` | Die Tools des Frameworks, ihre Berechtigungen und das Audit-Log |
| `mcp serve` / `mcp config` | Den MCP-Server betreiben / zeigen, wie man KI-Clients verbindet |
| `secrets set` / `secrets list` | Ein Token speichern (Hugging Face, GitHub) / die gespeicherten Namen auflisten |
| `module create` / `module generate-ai` | Ein neues Hardware-Target anlegen, per Fragen oder mit Ditto aus einer Probe-Datei |
| `scan` | Die Hardware-Probe auf diesem Rechner oder mit `--device` auf einem Board per SSH ausführen, oder mit `--import` die Probe-Datei eines Boards ablegen, und ihre Probe-ID zeigen |
| `device list` / `device settings` | Die untersuchten Boards / wie die Laufzeit auf einem Board installiert wird (Startskript oder systemd), vor einem Deployment entschieden |
| `device runtime plan/build/deploy/verify/status` | Die selbst gebaute llama.cpp-Laufzeit eines Boards: aus seinem Profil planen, cross-kompilieren, per SSH installieren und dort prüfen |
| `config show` / `config repos` | Die wirksamen Einstellungen und ihre Herkunft zeigen / Überschreibungen für Quell-Repositorys verwalten |
| `zoo list` / `zoo show` | Den Community-Zoo und die installierten Pakete auflisten / einen Eintrag zeigen |
| `zoo install` / `zoo verify` | Ein Community-Paket nach Prüfungen und deiner Bestätigung installieren / installierte Pakete oder eine Paketdatei prüfen |
| `zoo export` / `zoo keys` | Ein signiertes Paket aus deinem Target, Build oder deiner Wissensbasis machen (und einen Pull-Request öffnen) / deine öffentlichen Schlüssel zeigen |

---

## doctor

```
llm-cli doctor [--json] [--fix] [--tier 1|2]
```

Prüft Python, den Docker-Daemon und buildx, den freien Speicherplatz für den Cache- und den Ausgabeordner,
die Targets und ihre Capabilities, ob der persönliche Ordner privat ist, den Secrets-Speicher und den
Schlüsselbund, die Tool-Berechtigungen, das Audit-Log, den Chat-Verlauf (unveröffentlicht: er lässt sich
schreiben, seine neuesten Einträge stimmen), die mitgelieferten Kalibrierungsdaten und die installierten
Community-Pakete (zurückgezogen, verändert oder aus einer Datei importiert). Zu jedem Befund gibt es einen Hinweis,
wie du ihn behebst.

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe (Befunde, Korrekturen, Exit-Code). |
| `--fix` | Die automatischen Korrekturen bis `--tier` anwenden, dann erneut prüfen. |
| `--tier INTEGER RANGE` | `1` (Standard): harmlose Korrekturen (macht den persönlichen Ordner privat). `2`: entfernt auch Caches (verwaiste Framework-Images und den Docker-Build-Cache). |

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

Listet die Targets im eingestellten Targets-Ordner auf. Die Tabelle zeigt für jedes Target die ID, den Namen (zum
Beispiel „Rockchip Family“), die Architektur sowie die Boards und Formate, die sein Block `capabilities:` angibt; ein
Target ohne diesen Block ist mit „no capabilities declared“ markiert. **Der Wert für `--target` ist die ID** (zum
Beispiel `Rockchip`); der Ordnername des Targets funktioniert ebenso. `json` und `yaml` liefern dieselben Daten mit
den Quantisierungen je Format.

| Option | Bedeutung |
|---|---|
| `-f, --format [table\|json\|yaml]` | Ausgabeformat (Standard `table`). |

### targets validate

```
llm-cli targets validate TARGET_NAME
```

Prüft, ob das Target lädt (per ID oder Ordnername) und ob sein Block `capabilities:` das Schema besteht, das GUI,
CLI, Build-Service und Werkzeuge verwenden; jeder Schemafehler wird ausgegeben. Exit-Code 1, wenn das Target nicht
gefunden wird oder seine Capabilities ungültig sind. Ein Target ohne Capabilities wird gemeldet, gilt aber als
gültig.

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
| `-t, --target TEXT` | **Pflicht.** Target-ID, wie `targets list` sie zeigt, z. B. `Rockchip`, oder der Ordnername des Targets. Groß- und Kleinschreibung spielt keine Rolle. Ein unbekanntes Target stoppt mit Exit-Code 2 und der Liste der bekannten Targets. |
| `-f, --format [gguf\|rkllm\|onnx\|tflite\|pytorch_mobile\|rknn\|tensorrt\|openvino\|coreml\|ncnn\|mnn]` | Ausgabeformat (Standard `gguf`). Die Liste ist länger als das, was ein Target unterstützt; Rockchip nimmt `gguf`, `rkllm` und `rknn`. |
| `-b, --board TEXT` | Board der Target-Familie, z. B. `rk3588`, `rk3576`, `rk3566`. Standard: das Standard-Board des Targets (Rockchip: `rk3588`). Groß- und Kleinschreibung spielt keine Rolle. |
| `-q, --quantization TEXT` | Quantisierung, z. B. `Q4_K_M`, `IQ4_XS`, `F16` (GGUF), `w8a8`, `w4a16` (RKLLM), `fp16`, `i8` (RKNN). Standard: der Standard des Formats für das Board. Gängige Schreibweisen wie `INT8` werden übersetzt. |
| `-o, --output-dir TEXT` | Ausgabeordner, relativ zum aktuellen Ordner. Standard: `output/build_<Datum>_<Uhrzeit>` in der Installation. |
| `--optimization [fast\|balanced\|size\|speed\|aggressive]` | Optimierungsstufe (Standard `balanced`), wird den Build-Skripten als `OPTIMIZATION_LEVEL` übergeben. Die Rockchip-Skripte werten sie nicht aus. |
| `--priority [low\|normal\|high\|urgent\|critical]` | Position in der Build-Warteschlange dieses Prozesses (Standard `normal`). |
| `--parallel` | Veraltet und in `--help` ausgeblendet: Hat keine Wirkung (ein Aufruf startet einen Build) und gibt eine Warnung aus. |
| `-F, --follow` | Jede Stufe ausgeben, während der Build läuft. |
| `--gpu / --no-gpu` | Die GPU des Hosts in den Container durchreichen (Standard aus): eine NVIDIA-GPU, wenn `nvidia-smi` gefunden wird, sonst `/dev/dri`, falls vorhanden. Rockchip-Konvertierungen brauchen das nicht. |
| `--imatrix / --no-imatrix` | Importance Matrix für GGUF (Standard aus). Pflicht für `IQ1_S`, `IQ1_M`, `IQ2_XXS`, `IQ2_XS`, `IQ2_S`. |
| `--dataset PATH` | Deine eigene Kalibrierungsdatei (muss existieren): eine Textdatei für die GGUF-Importance-Matrix oder für RKLLM, eine Liste mit Beispieleingaben für RKNN `i8`. Ohne sie nutzt die Importance Matrix den mitgelieferten Kalibrierungstext. |

Verhalten:

- Der Befehl bleibt im Vordergrund, bis der Build fertig ist. Strg+C bricht den Build über den Build-Service ab:
  Sein laufender Container (Image-Build, Sicherheits-Scan oder Konvertierung) wird gestoppt, die CLI wartet, bis der
  Build angehalten hat (höchstens 120 s), sagt das und endet mit Exit-Code 130. In den Ausgabeordner wird nichts
  geschrieben. Ein Build, der seine Dateien schon in den Ausgabeordner verschiebt, lässt sich nicht mehr abbrechen;
  die CLI meldet dann, dass er von selbst zu Ende gelaufen ist.
- Scheitert der Build an einem bekannten Problem mit einer Korrektur, die das Framework selbst anwenden kann, zeigt
  die CLI die Ursache und fragt „Apply this fix and retry the build?“. Ohne Terminal wird der Vorschlag verworfen.
  Manuelle Schritte werden nur angezeigt.
- Ergebnisse: `<Ausgabeordner>/<Target>/<Modell>/<Quantisierung>/` mit der Modelldatei, `build_info.json` und
  `Model_Card.md`, dazu ein ZIP davon neben diesem Ordner. Sie erscheinen dort erst, wenn der Build abgeschlossen
  ist; ein abgebrochener oder gescheiterter Build hinterlässt dort keine Dateien.
- Zugangsbeschränkte Hugging-Face-Modelle brauchen ein Token: `llm-cli secrets set hf_token`. Gesendet wird nur das
  im Framework gespeicherte Token; ein Token in `HF_TOKEN` oder aus `hf auth login` wird nie verwendet.
- Ein Hugging-Face-Modell wird nach `<cache_dir>/models/hf/<owner>--<name>/<commit>/` heruntergeladen, ein Ordner pro
  Commit, Datei für Datei; nur dieser Ordner wird in die Build-Container eingebunden. Ein zweiter Build desselben
  Commits verwendet ihn wieder, und ein Build eines anderen Commits vermischt nie Dateien damit. Strg+C stoppt den
  Download vor seiner nächsten Datei.
- Ein volles Laufwerk wird als `insufficient_resources` mit dem freien Platz der Cache- und Ausgabelaufwerke
  gemeldet, und der Download stoppt, wenn das Cache-Laufwerk unter die Hälfte des Platzes fällt, der frei bleiben
  muss (2 GiB oder 2 % des Laufwerks). Der Self-Healing-Vorschlag für ein volles Laufwerk nennt die Ordner des
  Frameworks auf diesem Laufwerk mit ihrer Größe als manuellen Schritt; Docker aufzuräumen wird nur vorgeschlagen,
  wenn Dockers Datenverzeichnis das volle Laufwerk ist.
- Eine lokale Modelldatei wird allein in die Build-Container eingebunden, nie der Ordner, in dem sie liegt; ein
  Modell, das Dateien daneben braucht (zum Beispiel externe ONNX-Daten), wird als Ordner angegeben. Ditto und
  MCP-Clients können gar keinen Pfad nennen: Sie verwenden `<Label>:<relativer Pfad>` in den
  [Ordnern, die die KI nutzen darf](#tools-folders).
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

Zeigt Builds aus dem gemeinsamen Build-Zustand: Jedes Programm, das baut (CLI, GUI, MCP-Server), schreibt eine
Zusammenfassung jeder Build-Anfrage in den Benutzerordner (`build_requests/`) und je Build einen Eintrag in den
Cache-Ordner (`<cache_dir>/build_records/`) mit Request, Modell, Target, Format, Quantisierung, Status, Stufe und
Fortschritt, deshalb erscheinen auch Builds, die ein anderer Prozess gestartet hat, einschließlich Anfragen, die
noch in seiner Warteschlange warten (Spalte `Where`: das Programm, das sie ausführt). Ohne ID: eine Tabelle
der 20 neuesten Builds; mit einer Request-ID (`req_...`) oder einer Build-ID: die Einzelheiten jedes Versuchs,
einschließlich des Fehlers eines gescheiterten Builds (Zugangsdaten geschwärzt). Ein Build, dessen Prozess endete,
bevor der Build fertig war, erscheint als `interrupted`. Exit-Code 1, wenn die ID nicht gefunden wird. Der Befehl
braucht weder den Tresor für Zugangsdaten noch Docker. Unter Linux und macOS sind der Ordner der Einträge und die
Einträge nur für den Benutzer lesbar, der gebaut hat (Modi 0700 und 0600), sodass in einer gemeinsamen Installation
jeder Benutzer seine eigenen Builds sieht.

| Option | Bedeutung |
|---|---|
| `-a, --all` | Jeden aufbewahrten Build statt der 20 neuesten zeigen (aufbewahrt werden die 200 neuesten fertigen oder unterbrochenen Builds). |

Ein Request, der auf eine Self-Healing-Entscheidung wartet, zeigt seinen gescheiterten ersten Versuch; entscheide in
dem Programm, das ihn ausführt. Mit einer Request-ID folgen die Korrekturvorschläge, die KI-Clients dafür gemacht
haben (`propose_healing_fix`), jeder als KI-erzeugt markiert mit dem, der ihn vorgeschlagen hat (der Aufrufer und der
eigene, selbst angegebene Name des Clients), und seinem manuellen Schritt, den das Framework nie ausführt. Ohne ID
sagt eine Zeile, für wie viele gescheiterte Builds es solche Vorschläge gibt.

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
| `--docker / --no-docker` | Zusätzlich Container-Images einpacken (Standard aus). Experimentell und in dieser Version nicht geprüft; für eine Modelldatei aus lassen. Jedes Image (`image_inference_runtime` und mit Wissensdatenbank `image_qdrant`) muss per Digest festgelegt sein (`name@sha256:...`) oder auf diesem Rechner gebaut worden sein; ein Image, das nur über ein Tag benannt ist, etwa das voreingestellte `...:latest`, wird mit Grund abgelehnt, und ein fehlgeschlagener Image-Export bricht das Paket ab. |

Ohne Docker werden nur Modelldateien übertragen; Builds mit mehreren Gewichtsdateien werden abgelehnt. Ein Paket,
das nicht erstellt werden kann, gibt den Grund aus und endet mit 1; das ZIP wird unter einem vorläufigen Namen
geschrieben und erst umbenannt, wenn es vollständig ist.

```bash
llm-cli deploy package build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 --profile Rockchip
```

### deploy run

```
llm-cli deploy run PACKAGE_PATH --ip IP --user BENUTZER [--port 22] [--password TEXT] [--trust-fingerprint TEXT]
                   [--label TEXT]
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
| `--label TEXT` | Dein Name für das Board in den vertrauten Geräten (Standard: der Name, den es schon hat, oder seine Adresse). |

Erste Verbindung: Die CLI zeigt den Fingerabdruck des Host-Schlüssels und fragt, ob du ihm vertraust. Vergleiche ihn
mit `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` auf dem Board. Ohne Terminal und ohne `--trust-fingerprint`
wird nichts übertragen. Ein Board, dessen Host-Schlüssel sich geändert hat, wird immer abgelehnt. Nach einem
erfolgreichen Deployment kommt das Board zu den [vertrauten Geräten](#deploy-devices) (im Audit-Log vermerkt), so dass
ein KI-Client später dorthin übertragen darf, jedes Mal mit deiner Bestätigung. `ssh` und `scp` (genutzt, wenn
paramiko fehlt) bekommen nie die Standardeingabe des Frameworks.

```bash
llm-cli deploy run output/deploy_SmolLM2-135M-Instruct-Q4_0_1791801300.zip --ip 192.168.1.50 --user rock

# In einem Skript, mit vorher geprüftem Fingerabdruck
llm-cli deploy run output/deploy_model_1791801300.zip --ip 192.168.1.50 --user rock \
  --trust-fingerprint SHA256:AbCdEf...
```

### deploy devices

```
llm-cli deploy devices list [--json]
llm-cli deploy devices add HOST --user BENUTZER [--port 22] [--label TEXT]
llm-cli deploy devices remove GERÄTE_ID
```

Die vertrauten Geräte (`~/.llm-framework/devices.json`): die einzigen Geräte, die KI-Clients und Ditto für
`deploy_to_device` nennen dürfen, und jedes solche Deployment wird trotzdem erfragt. Ein Eintrag enthält deinen
Namen für das Gerät, die Adresse, den Benutzer, den Host-Schlüssel, den du bestätigt hast, und ob der eigene
SSH-Schlüssel des Frameworks sich dort anmeldet (gekoppelt); die Geräte-ID wird aus dem Host-Schlüssel abgeleitet,
dieselbe ID, die `llm-cli device list` zeigt. Einträge schreibst nur du: `deploy run`, `scan --device` und
`device runtime deploy` nach einer erfolgreichen Anmeldung, das Deployment-Fenster der GUI nach einem Deployment und
`deploy devices add`. Kein Tool schreibt, ändert oder entfernt einen.

| Befehl | Bedeutung |
|---|---|
| `list` | Jeder Eintrag mit seinem Host-Schlüssel und ob dieser noch der vertraute in der `known_hosts` des Frameworks ist (`trusted`, `CHANGED in known_hosts`, `not in known_hosts`), gekoppelt und dem letzten Deployment. |
| `add HOST --user BENUTZER` | Nimmt ein Board auf, dem du vor dieser Version vertraut hast (sein Host-Schlüssel steht schon in der `known_hosts` des Frameworks). Braucht ein Terminal: Es zeigt den Fingerabdruck und fragt; es vertraut nie einem neuen Schlüssel, fragt nie nach einem Passwort und markiert das Board nur dann als gekoppelt, wenn der Schlüssel des Frameworks sich anmeldet. |
| `remove GERÄTE_ID` | Entfernt den Eintrag (im Audit-Log vermerkt); KI-Tools können das Gerät dann nicht mehr nennen. Sein Eintrag in `known_hosts` bleibt. |

Ein Eintrag ist nur nutzbar, solange die `known_hosts` des Frameworks genau den Schlüssel enthält, den er nennt:
Hast du dort einen geänderten Schlüssel entfernt (um einem neu installierten Board wieder zu vertrauen), vertraue dem
Board erneut mit `deploy run` oder `scan --device`.

---

## ditto

Ditto ist der eingebaute Assistent. Die meisten `ditto`-Befehle nutzen Anbieter und Modell aus den KI-Einstellungen
(GUI: **Tools > Configure AI Agent (Ditto)...**, oder `ai_provider` und `ai_model` in der `config.yml`).
Anmelden kannst du dich mit [`llm-cli ditto login`](#ditto-login) oder mit **Anmelden...** in den
KI-Einstellungen.

### ditto login

```
llm-cli ditto login ANBIETER [--method ID] [--with-token] [--from-env] [--base-url URL] [--workspace-id ID]
                    [--organization ID] [--project ID] [--profile NAME] [--insecure-storage] [--no-check] [--yes]
```

Meldet Ditto bei einem Anbieter an (`anthropic`, `openai`, `google`, `openrouter`, `ollama`, `custom`;
[`llm-cli ditto auth methods`](#ditto-auth-methods) listet sie mit ihren Verfahren). Ein Schlüssel kommt nie von der
Kommandozeile, wo ihn der Shell-Verlauf und die Prozessliste behalten würden: Er wird verdeckt abgefragt, von stdin
gelesen (`--with-token`) oder einmal aus der dokumentierten Umgebungsvariable des Anbieters übernommen
(`--from-env`). Bevor er im Tresor gespeichert wird, prüft ihn eine kostenlose Anfrage (die Modellliste, bei
OpenRouter der Schlüssel-Endpunkt); ein abgelehnter Schlüssel wird nicht gespeichert. Ein Schlüssel mit einem
Leerzeichen, einem Zeilenumbruch oder einem unsichtbaren Zeichen darin wird abgelehnt, bevor etwas gesendet wird. Das
Verfahren wird als `ai_auth_<anbieter>` gemerkt. Die Ausgabe zeigt höchstens die letzten vier Zeichen eines
Schlüssels, auch wenn ein Fehlertext des Servers ihn wiederholt.

| Art des Verfahrens | Was `ditto login` tut |
|---|---|
| `api_key` (Cloud-Anbieter, `custom`-Server) | Fragt den Schlüssel ab, prüft ihn und speichert ihn. Der Schlüssel eines Servers, dessen Adresse du festlegst, gehört nur zu dieser Adresse. Admin-Schlüssel (`sk-ant-admin01-`) werden abgelehnt, bevor etwas gesendet wird. |
| `bearer_optional` (lokale Engines mit optionalem Schlüssel) | Fragt ein Token ab („Enter für keins“); ohne Token wird der Server ohne Schlüssel genutzt. Ein leeres stdin mit `--with-token` ist ein Bedienfehler und löscht nie ein gespeichertes Token; das macht `ditto logout`. |
| `none` (Ollama, `custom` ohne Schlüssel) | Speichert nichts; prüft die Adresse und zählt die Modelle. |
| `cli_session` (`--method ant_profile`) | Nutzt ein Profil von Anthropics eigener `ant`-CLI; nur der Name des Profils wird gespeichert. Schlägt die Prüfung über das Anthropic-SDK fehl, sagt der Befehl, dass du `ant auth login` ausführen sollst. Erweitert: wird nie automatisch gewählt, und Anthropic hat nicht bestätigt, dass andere Apps diese Profile nutzen dürfen. |
| OAuth- und Cloud-Anmeldungen | In dieser Version nicht verfügbar (Exit-Code 6). |

Ohne Terminal (Skripte, CI) wird nichts abgefragt: Ein Anbieter mit mehr als einem Verfahren braucht `--method`
(ein Verfahren, das nur auf Wunsch gilt, wie `ant_profile`, zählt nicht mit), und der Schlüssel muss über
`--with-token` oder `--from-env` kommen.

| Option | Bedeutung |
|---|---|
| `--method ID` | Anmeldeverfahren, siehe `llm-cli ditto auth methods ANBIETER`. |
| `--with-token` | Den Schlüssel von stdin lesen. |
| `--from-env` | Den Schlüssel aus der Umgebungsvariable des Anbieters (zum Beispiel `ANTHROPIC_API_KEY`) in den Tresor übernehmen; danach wird die Variable nicht mehr gebraucht. |
| `--base-url URL` | Serveradresse bei Anbietern, deren Adresse du festlegst (Ollama, `custom`); gemerkt als `ai_base_url_<anbieter>`. Ein Schlüssel, der über reines `http://` an einen anderen Rechner geht, braucht `--yes`, weil er unverschlüsselt übertragen wird. |
| `--workspace-id ID` | Claude-API: Workspace-ID (`wrkspc_...`) für Schlüssel, die in jedem Workspace der Organisation gelten; wird mit dem Schlüssel gespeichert. |
| `--organization ID`, `--project ID` | OpenAI: Organisation (`org-...`) und Projekt (`proj_...`), die mit dem Schlüssel gesendet werden. |
| `--profile NAME` | Mit `--method ant_profile`: das Profil (Standard: das aktive Profil der `ant`-CLI). |
| `--insecure-storage` | Ohne nutzbaren Schlüsselbund: den Schlüssel des Tresors in einer Datei neben dem Tresor ablegen (**im Ruhezustand nicht verschlüsselt**). Wird ignoriert, wenn der Tresor auf eine andere Art geschützt ist, die funktioniert; einen Credential-Helper oder eine Passphrase, die du gewählt hast (`secret_storage`), ersetzt es nie (Exit-Code 5). |
| `--no-check` | Ohne Prüfung speichern (zum Beispiel offline). |
| `-y, --yes` | Nicht nachfragen, bevor ein Schlüssel über reines `http://` gesendet oder `--insecure-storage` verwendet wird. |

Exit-Codes: 0 angemeldet; 2 Bedienfehler (unbekannter Anbieter oder unbekanntes Verfahren, fehlendes `--method`,
fehlender Schlüssel oder fehlende Serveradresse, ein fehlerhafter Optionswert oder Schlüssel); 3 abgelehnt
(falscher, widerrufener oder Admin-Schlüssel, ein Problem mit dem Workspace); 4 Anbieter oder lokaler Server nicht
erreichbar; 5 der Tresor lässt sich nicht nutzen (die Meldung nennt die Möglichkeiten); 6 das Verfahren ist in
dieser Version nicht verfügbar oder der Anbieter wurde entfernt.

```bash
llm-cli ditto login anthropic                      # fragt den Schlüssel ohne Anzeige ab
printf %s "$OPENAI_API_KEY" | llm-cli ditto login openai --with-token --project proj_abc123
llm-cli ditto login anthropic --from-env --workspace-id wrkspc_01AbCd
llm-cli ditto login custom --method api_key --base-url http://127.0.0.1:8000/v1
llm-cli ditto login ollama --base-url http://192.168.1.20:11434/v1
llm-cli ditto login anthropic --method ant_profile --profile work
```

`ditto login` meldet an; welchen Anbieter Ditto nutzt (`ai_provider`, in den KI-Einstellungen), ändert es nicht.

### ditto logout

```
llm-cli ditto logout ANBIETER [--method ID] [--base-url URL]
```

Meldet Ditto ab: Der gespeicherte Schlüssel (bei einem Server, dessen Adresse du festlegst, der Schlüssel dieser
Adresse) wird von diesem Rechner gelöscht, und das gemerkte Verfahren wird vergessen. Der Schlüssel selbst
funktioniert weiter, bis du ihn auf der Schlüsselseite des Anbieters widerrufst; der Befehl nennt sie. Ein
Schlüssel in einer Umgebungsvariable bleibt in Gebrauch, bis du die Variable entfernst; der Befehl sagt das. Beim
`ant`-Profil wird nur der Name des Profils vergessen: `ant auth logout` würde auch die anderen Werkzeuge abmelden,
die es nutzen, deshalb überlässt der Befehl das dir.

| Option | Bedeutung |
|---|---|
| `--method ID` | Verfahren, von dem abgemeldet wird (Standard: das genutzte). |
| `--base-url URL` | Adresse, deren Schlüssel gelöscht wird (Standard: die eingestellte). |

### ditto auth status

```
llm-cli ditto auth status [--json] [--check]
```

Eine Zeile pro Anbieter: Verfahren, woher die Zugangsdaten kommen (`vault`, `env:<VARIABLE>`, `profile:<name>`,
`runtime`), Konto oder Workspace, die letzten vier Zeichen des Schlüssels und der Zustand („Signed in as ...“,
„Not signed in“, „Using key from environment variable X“, „Expired: sign in again“). Dittos Anbieter ist mit `*`
markiert. Ein Schlüssel wird nie gezeigt.

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe, ebenfalls ohne Schlüssel. |
| `--check` | Zusätzlich die kostenlose Prüfung für jeden Anbieter mit Zugangsdaten ausführen (und für Dittos Anbieter). |

Exit-Code 5, wenn sich der Tresor nicht nutzen lässt; mit `--check` der Code der ersten gescheiterten Prüfung (3
oder 4); sonst 0.

### ditto auth methods

```
llm-cli ditto auth methods [ANBIETER] [--json]
```

Die Anmeldeverfahren aller Anbieter (oder eines Anbieters) in der Reihenfolge, in der sie bevorzugt werden: das
empfohlene, Verfahren, die nur auf Wunsch gelten, Verfahren, die in einer späteren Version kommen, die
Schlüsselseite, die Umgebungsvariablen, die zusätzlichen Optionen (`--workspace-id`, `--organization`, `--project`)
und Hinweise zu den Nutzungsbedingungen. Braucht weder den Tresor noch das Netz.

### ditto auth storage

```
llm-cli ditto auth storage [--use keyring|helper|passphrase|insecure] [--helper BEFEHL] [--yes]
```

Ohne `--use` zeigt der Befehl, wie der Tresor geschützt ist (siehe [secrets](#secrets)), und endet mit Exit-Code
5, wenn er sich nicht nutzen lässt. Mit `--use` wird der Tresor ab jetzt auf die gewählte Art geschützt: Ein
funktionierender Tresor wird mit einem neuen Schlüssel neu verschlüsselt, ein Tresor, der sich nicht öffnen ließ
(kein Schlüsselbund), wird mit dem neuen Schutz geöffnet, und die Wahl wird als `secret_storage` (und
`secret_helper`) gespeichert. Für dich wird nichts gewählt. `ephemeral` wählst du je Lauf mit
`LLM_SECRET_STORAGE=ephemeral` (hier Exit-Code 2). Eine neue Passphrase wird zweimal im Terminal abgefragt; ohne
Terminal endet der Befehl mit Exit-Code 5, und nichts ändert sich.

```bash
llm-cli ditto auth storage --use passphrase
llm-cli ditto auth storage --use helper --helper "pass show llmcc/vault-key"
```

### ditto auth rotate-key

```
llm-cli ditto auth rotate-key
```

Erzeugt einen neuen Schlüssel für den Tresor, verschlüsselt alle gespeicherten Zugangsdaten damit neu und entfernt
den alten Schlüssel. Exit-Code 5, wenn sich der Tresor nicht nutzen lässt.

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

### ditto remote

```
llm-cli ditto remote set --transport stdio|http [--command "..."] [--url URL] [--name TEXT] [--with-token]
llm-cli ditto remote show | test | off
```

Ditto und das Framework auf einem anderen Computer (optional): Ditto nutzt zusätzlich das Framework, das auf einem
zweiten Computer installiert ist (ein stärkerer Build-PC, ein Server), über den MCP-Server jener Installation, neben
den Tools dieses Computers, die immer bleiben; jeder Aufruf geht zu dem Computer, auf dem sein Objekt liegt. Es ist
dasselbe Programm, kein anderes, und Docker ist nicht nötig: Verbinde dich über SSH,
entweder mit einem Tunnel zur Adresse des Servers oder mit `ssh` als Befehl
([Einzelheiten](Features_DE.md#ditto-und-ein-framework-auf-einem-anderen-computer)). Es gelten Registry,
Berechtigungen, Rückfragen und Audit-Log jenes Frameworks für die Aufrufe, die dort laufen, und das Audit-Log dieses
Computers hält jeden davon ebenfalls fest (`started` und sein Abschluss, mit `place remote:<Name>`). Lege zuerst auf
dem anderen Computer das Token mit `llm-cli mcp token add NAME --link ditto` an: Das Token nutzt dort Dittos eigene
Berechtigungen, und das Audit-Log dort zeigt `mcp:NAME`. `set` speichert das Token im Tresor für
Zugangsdaten dieses Computers (ohne Echo abgefragt oder mit `--with-token` von stdin gelesen) und die Verbindung in
Dittos KI-Einstellungen; das Token läuft nie durch ein Tool, ein Ergebnis oder eine Befehlszeile. Über stdio startet
Ditto `--command` (zum Beispiel `ssh -T -o BatchMode=yes -o SendEnv=LCF_MCP_TOKEN benutzer@build-box llm-cli mcp
serve`, das `AcceptEnv LCF_MCP_TOKEN` in der Konfiguration des SSH-Servers dort braucht und `llm-cli` dort als Befehl,
nicht nur als Alias, siehe [`llm-cli` aufrufen](../README_DE.md#llm-cli-aufrufen); oder, für einen
Framework-Container, den du selbst gebaut hast, `docker exec -i lcf llm-cli mcp serve`) mit dem Token in
`LCF_MCP_TOKEN`. `docker exec` und `podman exec` geben ihre Umgebung nicht in den Container weiter, darum ergänzt
Ditto bei einem solchen Befehl `-e LCF_MCP_TOKEN` (nur den Namen, nie den Wert); und nennt der erreichte Server statt
des Tokens den gemeinsamen lokalen Aufrufer `mcp` (zum Beispiel über `ssh` ohne `SendEnv`, das die Umgebung
verwirft), nutzt Ditto ihn nicht und sagt, wie die Variable weitergegeben wird. Über HTTP sendet es das Token als
Bearer-Header an `--url`, das https oder eine Loopback-Adresse sein muss, etwa das lokale Ende eines SSH-Tunnels
(`ssh -N -L 8765:127.0.0.1:8765 benutzer@build-box`, dann `http://127.0.0.1:8765/mcp`). Ohne Token wird der andere
Computer nicht genutzt. Ein- oder Ausschalten beginnt nie eine neue Unterhaltung; ein nicht erreichbarer Computer
hält nie eine Frage an (Ditto macht mit den Tools dieses Computers weiter, und `ditto ask` gibt einen Hinweis auf
stderr aus). Jedes Ergebnis, jeder Fehler und jede Rückfrage des anderen Frameworks erreicht Dittos Modell als nicht
vertrauenswürdige Daten, seine Tool-Beschreibungen nie, und Dittos Bestätigung nennt den Server. `test` verbindet
sich, listet die Tools und Aufgaben-Prompts, die der Server Ditto anbietet, und sagt, wie sie zu denen dieses
Computers kommen (einmal angeboten, nur dort, getrennt); `show` zeigt die Einstellungen (nie das Token); `off` lässt
Ditto wieder mit diesem Computer allein arbeiten. Die KI-Einstellungen der GUI („Another computer
(optional)“) setzen dieselben Werte; auch dort landet das Token im Tresor und wird nie wieder angezeigt.

```bash
# Auf dem anderen Computer: llm-cli mcp token add ditto-laptop --link ditto, dann llm-cli mcp serve --transport http
ssh -N -L 8765:127.0.0.1:8765 benutzer@build-box &   # der Tunnel (offen lassen)
llm-cli ditto remote set --transport http --url http://127.0.0.1:8765/mcp --name "Build-Rechner"
llm-cli ditto remote test
```

### ditto eval

```
llm-cli ditto eval [Optionen]
```

Bewertet Ditto anhand fester Testfälle (Analyse von Build-Logs, Build-Empfehlungen, Modulvorschläge, Chat mit Tools,
Prompt-Injection), zum Beispiel um zu prüfen, ob ein lokales 2-Bit-Modell gut genug ist. Mit `--kind retrieval`
bewertet es stattdessen die Suche der Wissensbasis, ganz ohne KI-Modell ([unten](#retrieval-evaluation)). Nichts
wird gebaut oder geändert: Chat-Fälle laufen gegen einen Ersatz-Build-Dienst. Cloud-Anbieter berechnen jede Anfrage
(etwa 20 pro Lauf und Wiederholung). Wie es funktioniert: [Features_DE.md](Features_DE.md#ditto-evaluation).

| Option | Bedeutung |
|---|---|
| `--cases FILE` | Datei mit den Fällen. Standard: `evals/ditto/cases.yml` der Installation, für `--kind retrieval` `evals/retrieval/cases.yml`. |
| `--provider TEXT` | Anbieter für den Lauf. Standard: KI-Einstellungen. |
| `--model TEXT` | Modell für den Lauf. Standard: KI-Einstellungen. |
| `--tier TEXT` | Gegen das verwaltete lokale Modell dieser Stufe laufen; wiederholbar (`--tier small --tier small-q2`). Nicht mit `--provider`/`--model` kombinierbar (Exit-Code 2). |
| `--kind [healing\|optimizer\|module\|chat\|retrieval]` | Nur Fälle dieser Art; wiederholbar. `retrieval` läuft nur, wenn es hier gewählt ist, wenn `--case` ohne `--kind` angegeben ist oder wenn die Datei aus `--cases` Retrieval-Fälle enthält. |
| `--rag-variant [fused\|rerank]` | Nur Retrieval: die zu bewertende Suchvariante; wiederholbar, ein Lauf je Variante. `fused` (Standard): die hybride Suche. `rerank`: die hybride Suche plus der Reranker aus `rag_reranker` mit seiner Relevanzschwelle. |
| `--case TEXT` | Nur diese Fall-ID; wiederholbar. |
| `--repeat INTEGER RANGE` | Läufe pro Fall, 1 bis 20 (Standard 1). |
| `--json FILE` | Den vollständigen Bericht (Einrichtung, Bestehensquoten, jede Antwort) als JSON schreiben. |
| `--min-pass FLOAT RANGE` | Exit-Code 1, wenn ein Lauf weniger Fälle besteht als dieser Anteil (0 bis 1, z. B. `0.8`). |
| `--effort [low\|medium\|high]` | Aufwand für diesen Lauf (lokale Modelle: `low` = ohne Nachdenken). Standard: KI-Einstellungen. |

Mit `--tier` muss die lokale Laufzeit installiert und das Modell der Stufe heruntergeladen sein. Die CLI startet
`llama-server` nacheinander mit jeder Stufe und stoppt die Server, die sie gestartet hat. Ein Server, den du selbst
gestartet hast, läuft weiter, wenn er die Stufe schon bedient; sonst ersetzt die Evaluation ihn für diese Stufe und
sagt das am Ende.

```bash
# Das 2B-Modell mit 4 und 2 Bit auf diesem Rechner vergleichen, ohne Nachdenken
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json

# Nur die Chat-Fälle, je drei Läufe, gegen den eingestellten Anbieter
llm-cli ditto eval --kind chat --repeat 3

# Als Qualitätsschwelle in der CI
llm-cli ditto eval --provider anthropic --min-pass 0.8
```

Das Ergebnis ist eine Tabelle pro Lauf: bestandener Anteil, Anteil pro Art und durchschnittliche Sekunden pro Fall.
Eine Datei mit Fällen, die sich nicht lesen lässt, oder Filter, auf die kein Fall passt, stoppen mit Exit-Code 2.

#### Retrieval-Evaluation

`llm-cli ditto eval --kind retrieval` misst, wie gut die Wissensbasis den richtigen Auszug findet. Die
mitgelieferten Fälle sind 42 Suchen auf Deutsch und Englisch: Fragen über die Sprachgrenze hinweg, Bezeichner wie
`GGML_CUDA`, Antworten tief in einer großen Tabelle oder einem Skript, die letzten Zeilen gescheiterter Builds, die
Suchbegriffe, die Ditto selbst schickt, und 4 Fragen ohne Bezug. Sie laufen gegen einen Korpus aus 30 Auszügen der
Framework- und SDK-Dokumentation in `evals/retrieval/corpus/` (eine Datei mit Fällen nennt ihren Korpus-Ordner mit
`corpus:`, relativ zur Datei).

- Der Korpus wird mit dem eingestellten lokalen Embedding-Modell (`rag_embedding_model`) in eine temporäre
  Wissensbasis indiziert, die danach gelöscht wird. Deine eigene Wissensbasis bleibt unberührt, und weder ein
  KI-Modell noch ein Qdrant-Server wird genutzt. `--provider`, `--model`, `--tier` und `--effort` wirken auf diese
  Fälle nicht.
- Das Embedding-Modell und für `rerank` der Reranker werden beim ersten Mal in den Modellordner heruntergeladen
  (nicht im Offline-Modus); danach funktioniert die Evaluation offline.
- Jede Suche liefert bis zu 10 Auszüge. Ein Fall besteht, wenn der erste passende Auszug innerhalb seiner Grenze
  liegt (`within` des Falls, Standard 3); eine Frage ohne Bezug besteht nur, wenn kein Auszug zurückkommt.
- Neben der Tabelle der Läufe gibt es eine Tabelle „Retrieval“ mit einer Zeile je Variante: hit@1, hit@3, hit@k
  (k = die Grenze des jeweiligen Falls), MRR über die 10 Ergebnisse, „Unrelated: no excerpt“ (der Anteil der Fragen ohne Bezug, die
  nichts bekommen haben) und die mittlere Suchzeit („p50 ms“, Median). `--json` ergänzt für jeden Fall die
  gefundenen Dokumente und ihre Werte, die Prüfsummen von Fällen und Korpus, die Dauer der Indizierung und den
  belegten Speicher.
- `rerank` braucht einen Reranker. Mit `rag_reranker: off` oder mit `auto` auf einem Rechner mit weniger als 6 GB RAM
  gibt es keinen, und der Befehl stoppt mit Exit-Code 2 und dem Grund; setze `rag_reranker` auf eine Modell-ID, um
  trotzdem einen zu bewerten. Lässt sich der Reranker nicht laden, endet der Lauf mit Exit-Code 1, und eine Suche,
  in der der Reranker nicht lief, zählt als gescheitert. Die Schwelle ist `rag_rerank_min_score` (standardmäßig
  nicht gesetzt: die kalibrierte Schwelle des Rerankers; 0: keine).
- Ein Korpus, der sich nicht lesen lässt, oder ein Fall, der ein Dokument erwartet, das der Korpus nicht hat, stoppt
  mit Exit-Code 2. Lässt sich der Korpus nicht indizieren (zum Beispiel im Offline-Modus, bevor das Embedding-Modell
  heruntergeladen wurde), endet der Befehl mit Exit-Code 1.

Außerhalb der Evaluation nutzen nur Build-Empfehlungen den Reranker. Dittos Chat und das MCP-Tool
`search_knowledge` sortieren nur mit `rag_rerank_default: true` neu (Standard aus).

```bash
# Die hybride Suche mit dem eingestellten Embedding-Modell
llm-cli ditto eval --kind retrieval

# Hybride Suche und Reranker nebeneinander, Bericht als JSON
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json retrieval.json

# Nur zwei Fälle
llm-cli ditto eval --kind retrieval --case retr-en-rknn-onnx-convert --case retr-en-cuda-oom
```

### ditto runtime

Der verwaltete llama.cpp-Server für Dittos Offline-Modus. Diese Befehle funktionieren ohne Docker und ohne
Schlüsselbund. Dateien: `~/.llm-framework/runtime/llama.cpp/` (Builds und Zustand) und
`~/.llm-framework/logs/llama-server.log`. Die optionalen MCP-Tools `build_local_runtime`, `start_local_runtime`,
`stop_local_runtime` und `run_benchmark` tun dasselbe für einen KI-Client, sobald du sie freigibst
([Details](Features_DE.md#laufzeit-tools-für-ki-clients-optional)); `install --prebuilt` und das Installieren von
Toolchain-Teilen bleiben Befehle nur für dich.

#### ditto runtime status

```
llm-cli ditto runtime status [--json]
```

Zeigt die erkannte Hardware; den genutzten Build mit seiner Herkunft (`self-built`, `imported` oder `prebuilt, not
self-built`), Build-ID, llama.cpp-Commit, Backend, wo und wie lange er gebaut wurde, wann er geprüft wurde und welche
Geräte er gefunden hat; warum ein gespeicherter Build nicht genutzt werden kann; ob ein neuer Build nötig (die
Festlegung hat sich geändert) oder empfohlen ist (der Bauplan dieses Rechners hat sich geändert); das GPU-Backend,
das das Bau-Rezept für diesen Rechner wählt, und warum; welches offizielle vorgebaute Archiv `--prebuilt`
installieren würde und warum (oder warum keines passt, zum Beispiel bei musl oder einer älteren glibc); ob der Server
läuft (Adresse, Prozess, Modell, Kontext); das empfohlene Modell und warum; und die Log-Datei. Die Prüfung auf einen
neuen Build erkennt die Toolchain (sie kompiliert nichts).

| Option | Bedeutung |
|---|---|
| `--json` | Maschinenlesbare Ausgabe (`installed`, `problems`, `rebuild`, `recommended_backend`, `prebuilt_option`, ...). |

#### ditto runtime install

```
llm-cli ditto runtime install [--backend auto|cpu|vulkan|cuda|rocm|metal] [--placement auto|native|container]
                              [--jobs N] [--prebuilt] [--force] [-y]
```

Baut `llama-server` (mit `llama-cli`, `llama-bench` und `llama-perplexity`) auf diesem Rechner aus dem festgelegten
llama.cpp-Commit (`configs/llama_runtime.yml`, `llama_cpp.source`). Es untersucht den Rechner, erkennt die Toolchain,
zeigt die geplanten Builds und fragt. Der CPU-Build wird zuerst gebaut, geprüft und aktiv gemacht (er bleibt der
Ersatz); danach wird der GPU-Build gebaut, den das Rezept wählt, und aktiv, sobald er geprüft ist. Ein GPU-Build, der
hier noch nicht gebaut werden kann oder scheitert, lässt den CPU-Build aktiv und wird gemeldet. Jeder Build wird auf
diesem Rechner geprüft, bevor er behalten wird: `--version` nennt die festgelegte Build-Nummer und den Commit,
`--list-devices` zeigt das geplante Gerät, und der Server lehnt `--tools` ab. Ein schon gespeicherter Build mit
derselben Build-ID wird wiederverwendet, nicht neu gebaut. Ein laufender Server läuft weiter; der nächste
`runtime start` nutzt den neuen Build.

- **Toolchain:** GCC oder Clang, CMake 3.15 oder neuer und Ninja (Debian/Ubuntu: `build-essential cmake
  ninja-build`). Ohne sie läuft der Build im Builder-Container des Frameworks (Docker, `--network none`). Ohne beides
  nennt der Befehl die Auswege und endet mit 1. Für dich wird nichts installiert.
- **Quellcode:** Der festgelegte Commit wird einmal geholt (etwa 40 MB Download, 215 MB auf der Platte), nachdem du
  zugestimmt hast, und über seinen Commit geprüft; auf einem Rechner ohne Netzwerk importiere ihn vorher
  (`ditto runtime import-source`). Konfiguration und Build laufen ohne Netzwerk.
- **Backends in dieser Version:** CPU und Vulkan unter Linux. CUDA- und ROCm-Builds sowie Windows und macOS folgen;
  auf einem Rechner mit NVIDIA-GPU läuft bis dahin der CPU-Build (`--backend vulkan` baut einen Vulkan-Server für
  die GPU).
- **Exit-Code 1** auch, wenn ein ausdrücklich verlangtes GPU-Backend nicht gebaut werden konnte (der CPU-Build ist
  aktiv).

| Option | Bedeutung |
|---|---|
| `--backend [auto\|cpu\|vulkan\|cuda\|rocm\|metal]` | GPU-Backend, das nach dem CPU-Build gebaut wird (Standard `auto`: das Bau-Rezept wählt; `cpu`: nur der CPU-Build). Mit `--prebuilt`: das zu installierende Archiv. |
| `--placement [auto\|native\|container]` | Wo der Build läuft (Standard `auto`: nativ, wenn die Toolchain vollständig ist, sonst im Builder-Container). |
| `--jobs INTEGER RANGE` | Parallele Compiler-Jobs, 1 bis 256. Standard: aus den CPUs und dem freien Speicher. |
| `--prebuilt` | Stattdessen das offizielle vorgebaute Archiv von upstream herunterladen (Größe und sha256 vor dem Entpacken geprüft). Nur auf diese Anforderung, immer als „prebuilt, not self-built“ angezeigt. Ein schon installiertes Archiv, auch eines, das eine frühere Version heruntergeladen hat (es wird ohne diese Anforderung nicht genutzt), wird übernommen, ohne es neu herunterzuladen. Linux-Archive brauchen glibc 2.35 oder 2.39; bei musl wird keines angeboten. |
| `--force` | Mit `--prebuilt`: erneut herunterladen und entpacken, auch wenn es installiert ist. |
| `-y, --yes` | Vor dem Bauen, dem Holen des Quellcodes oder dem Download nicht fragen. |

#### ditto runtime import

```
llm-cli ditto runtime import DATEI [-y]
```

Importiert einen `llama-server`-Build, der mit `ditto runtime export` auf einem anderen Rechner derselben Art gemacht
wurde (gleiches Betriebssystem und gleiche Architektur, und eine mindestens so neue C-Bibliothek). Das Archiv wird
sicher entpackt (die Größe wird vorher begrenzt), jede Datei gegen sein Manifest geprüft, die Build-ID gegen seinen
Plan, der Plan gegen die Bauregeln (keine Download-Option, keine Subprozess-Unterstützung) und der Commit gegen die
Festlegung. Ein mit `-march=native` gemachter Build wird abgelehnt, wenn diese CPU von einem anderen Hersteller ist
oder ihr eine CPU-Funktion der bauenden CPU fehlt (etwa AVX-512): Er bräche bei der ersten Rechnung mit SIGILL ab.
Dann zeigt der Befehl, wo der Build gemacht wurde, und fragt, bevor eines seiner Programme läuft; danach
wird der Build hier wie ein lokaler geprüft und wird der aktive Build (ein CPU-Build auch der Ersatz). Importiere nur
Builds, die du selbst gemacht hast.

| Option | Bedeutung |
|---|---|
| `-y, --yes` | Nicht fragen, bevor die importierten Programme für die Prüfung laufen. |

#### ditto runtime export

```
llm-cli ditto runtime export DATEI
```

Schreibt den genutzten selbst gebauten Build nach `DATEI` (`.tar.gz`: seine Programme, `build.json` und
`manifest.json`, keine Logs) für `ditto runtime import` auf einem anderen Rechner derselben Art. Das vorgebaute
Archiv wird nie exportiert.

#### ditto runtime prune

```
llm-cli ditto runtime prune [--keep N]
```

Entfernt alte selbst gebaute Builds von Dittos Server. Der aktive Build, der CPU-Ersatz und der Build, aus dem ein
laufender Server gestartet wurde, bleiben immer erhalten, dazu die `N` neuesten anderen (Standard 2).
`runtime install` tut dasselbe nach jedem Build. Die Builds deiner Boards (`llm-cli device runtime build`) liegen im
selben Build-Speicher und werden hier nie entfernt.

#### ditto runtime start

```
llm-cli ditto runtime start [--model STUFE] [--ctx N] [--sleep-idle SEKUNDEN]
```

Startet `llama-server` auf 127.0.0.1 mit einem freien Port und einem neuen API-Schlüssel in einer privaten Datei, mit
einer geleerten Umgebung und ohne die Optionen, die die gemeinsame Startregel verbietet. Gestartet wird nur ein Build,
der seine Prüfung auf diesem Rechner bestanden hat, noch zu seinem Manifest passt und vom festgelegten Commit ist (der
aktive Build, sonst sein CPU-Ersatz), oder das vorgebaute Archiv nach `install --prebuilt`; ein selbst gebauter
Build, der nicht genutzt werden kann, weicht nie auf das vorgebaute Archiv aus. Der Server läuft weiter, nachdem der
Befehl zurückkehrt, bis `llm-cli ditto runtime stop`. Läuft dasselbe Modell schon aus demselben Build, wird es
weiterverwendet.

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

#### ditto runtime export-source

```
llm-cli ditto runtime export-source DATEI [--mirror URL] [-y]
```

Schreibt den festgelegten llama.cpp-Quelltext (den Commit in `configs/llama_runtime.yml`, `llama_cpp.source`) als
Git-Bundle nach `DATEI`, dazu `DATEI.json` mit dem Commit, dem Tag, Größe und SHA256 des Bundles und der
Framework-Version. Beide Dateien werden auf einen Rechner ohne Internetzugang getragen und dort importiert. Liegt der
Quelltext noch nicht im Zwischenspeicher, wird er zuerst geholt (etwa 40 MB Download, 215 MB auf der Platte): per
Commit (über seinen Tag nur, wenn der Server das Holen per Commit ablehnt), und in jedem Fall muss
`git rev-parse HEAD` den festgelegten Commit zurückgeben. Der geprüfte Baum bleibt schreibgeschützt in
`~/.llm-framework/runtime/llama.cpp/src/<commit>/`; spätere Läufe prüfen, dass darin nichts geändert oder
hinzugefügt wurde (auch keine Dateien, die Git ignoriert), und brauchen kein Netz. Eine relative `DATEI` gilt relativ
zum aktuellen Ordner. Braucht `git`.

| Option | Bedeutung |
|---|---|
| `--mirror URL` | Von diesem Spiegel holen statt aus dem festgelegten Repository. Der Commit wird genauso geprüft. |
| `-y, --yes` | Ohne Nachfrage holen, wenn der Quelltext noch nicht im Zwischenspeicher liegt. |

#### ditto runtime import-source

```
llm-cli ditto runtime import-source DATEI
```

Importiert den festgelegten llama.cpp-Quelltext aus einem Bundle von `export-source` nach
`~/.llm-framework/runtime/llama.cpp/src/<commit>/`. Git prüft jedes Objekt an seinem Hash, und der ausgecheckte
Commit muss der festgelegte sein, egal wer das Bundle mit welcher Git-Version erstellt hat. Ein Bundle mit einem
anderen Commit, ein Bundle, das nicht zu seiner `DATEI.json` passt (Größe oder SHA256), und ein beschädigtes Bundle
werden abgelehnt. Braucht `git` auch auf dem Rechner ohne Netz.

```bash
# Rechner mit Internet
llm-cli ditto runtime export-source llama.cpp-source.bundle -y
# Rechner ohne Netz (vorher llama.cpp-source.bundle und llama.cpp-source.bundle.json kopieren)
llm-cli ditto runtime import-source llama.cpp-source.bundle
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

Die Tools des Frameworks sind das, was Ditto und MCP-Clients verwenden: 16 nur lesende Tools (`list_targets`,
`get_target_capabilities`, `list_build_requests`, `get_build_status`, `read_build_log`, `list_artifacts`,
`run_doctor`, `search_knowledge`, `list_knowledge_sources`, `read_knowledge_source`, `search_hf_models`,
`get_hf_model_info`, `list_local_files`, `get_status`, `get_job_status`, `list_devices`), 7, die auf diesem Rechner
etwas ändern (`start_build`, `cancel_build`, `resolve_healing`, `cancel_job`, `propose_healing_fix`,
`download_hf_file`, `create_deploy_package`), und `deploy_to_device`, das ein Gerät erreicht, dem du vertraust
(Stufe `external`: immer mit Frage).

### tools list

```
llm-cli tools list [--caller ditto|mcp]
```

Jedes Tool mit seiner Stufe (`read`, `local_write`), der aktuellen Berechtigung (`allow`, `ask`, `deny`) und einer
Beschreibung, gefolgt von der eigenen Vorgabe des Tools, der höchsten Entscheidung, die eine Einstellung ihm geben
kann (`at most`), ob es neu ist (noch nicht gesehen: es nutzt seine eigene Vorgabe, bis du es gesehen hast) und ob
eine Bestätigung eine dauerhafte Freigabe anbieten darf. Ohne `--caller` zeigt es die gemeinsamen Berechtigungen und
nennt die Aufrufer, die eigene haben. Der Titel sagt, wenn die Berechtigungen noch nicht eingerichtet sind.

### tools permissions

```
llm-cli tools permissions [--caller ditto|mcp | --token NAME] [--reset]
```

Stellt dieselben drei Fragen wie [`ditto setup`](#ditto-setup) und speichert die Antworten. Berechtigungen für
einzelne Tools, die in der GUI gesetzt wurden, bleiben erhalten. Vorher führt es jedes Tool mit seiner eigenen
Voreinstellung auf, der höchsten Entscheidung, die eine Einstellung ihm geben kann (`at most`), ob es seit dem
letzten Durchgang neu ist und ob eine Bestätigung eine dauerhafte Freigabe anbieten darf; das Speichern zählt jedes
aufgeführte Tool als gesehen. Der GUI-Dialog zeigt dieselben Angaben.

Es braucht eine Person an einem Terminal, mit und ohne `--reset` (wie `ditto setup`): Weitergeleitete Antworten werden
mit dem Exit-Code 2 abgelehnt. Jedes Speichern wird mit dem Akteur `cli` und den alten und neuen Entscheidungen
festgehalten, bevor es wirkt, und abgelehnt (Exit-Code 1, nichts geändert), wenn sich der Audit-Eintrag nicht
schreiben lässt oder die Berechtigungen anderswo gespeichert wurden (GUI, ein anderes Terminal), während du
geantwortet hast. Eine Berechtigungsdatei, die beschädigt, von Hand bearbeitet oder nicht signiert ist (auch eine von
3.0.0 gespeicherte), verbietet jedes Tool, bis du das hier erneut ausführst; die Fragen beginnen dann mit "deny".

| Option | Bedeutung |
|---|---|
| `--caller ditto\|mcp` | Die Berechtigungen eines Aufrufers setzen (Ditto oder lokale MCP-Clients über stdio) statt der gemeinsamen. Die ersten Berechtigungen eines Aufrufers beginnen mit den gemeinsamen, samt den Einstellungen für einzelne Tools. |
| `--token TEXT` | Stattdessen die Berechtigungen dieses [benannten MCP-Tokens](#mcp-token) setzen (nicht zusammen mit `--caller`). Braucht ein Terminal; bei einem an Ditto gebundenen Token wird zuerst gefragt, ob es eigene bekommen soll. Es führt dieselben Angaben zu den Tools für dieses Token auf. Protokolliert mit dem Akteur `cli`. |
| `--reset` | Zurück zu den empfohlenen Standardwerten (die Einrichtung fragt beim nächsten Mal erneut). Mit `--caller`: Dieser Aufrufer nutzt wieder die gemeinsamen Berechtigungen. Mit `--token`: zurück auf nur lesen. |

### tools audit

```
llm-cli tools audit [--limit N] [--details] [--verify] [--archive] [--summary]
```

Zeigt die letzten Tool-Aufrufe: Zeit, wer aufgerufen hat (in Worten: Ditto, alle lokalen stdio-Clients, das
MCP-Token '<Name>' oder du in der CLI oder GUI, mit dem Namen, den der Client selbst angegeben hat), das Tool mit
seinem Titel (und dem Namen, den die KI geschrieben hat, wenn das eine Schreibvariante war), die Entscheidung, ob ein
Mensch bestätigt hat, das Ergebnis, den Grundcode und wie der Mensch gefragt wurde, mit seiner Antwort (zum Beispiel
`elicitation: declined`).

| Option | Bedeutung |
|---|---|
| `--limit INTEGER` | Anzahl der Einträge (Standard 30). |
| `--details` | Zusätzlich, was die narrensichere Schicht repariert hat (Art und Feld), die Dauer, SHA-256 und Größe des Ergebnisses, Verweise auf frühere Einträge und den Sitzungs-Hash. |
| `--verify` | Stattdessen die Kette prüfen: geänderte oder entfernte Einträge und das Entfernen der neuesten Einträge (Anker-Datei). Exit-Code 0, wenn intakt, sonst 1. |
| `--summary` | Stattdessen Zahlen je Aufrufer: Aufrufe, Ablehnungen und die Meldungen, dass eine Aufgabe nicht machbar ist (`report_cannot_do`), nach dem Ergebnis der Prüfung durch das Framework: confirmed, contradicted, premature, unchecked. |
| `--archive` | Das Log und seinen Anker beiseitelegen (aufbewahrt als `audit.db.<UTC-Zeit>` und `audit.db.<UTC-Zeit>.anchor` im persönlichen Ordner) und mit dem nächsten Tool-Aufruf ein neues Log beginnen. Nutze es, wenn `--verify` oder `llm-cli doctor` ein Problem mit dem Log meldet. Lässt sich das Log nicht beiseitelegen (zum Beispiel, weil es gerade benutzt wird), endet der Befehl mit Exit-Code 1; schließe die GUI und die KI-Clients und versuche es erneut. |

```bash
llm-cli tools audit --limit 100
llm-cli tools audit --verify
llm-cli tools audit --summary
llm-cli tools audit --archive
```

### tools approvals

```
llm-cli tools approvals [list]
llm-cli tools approvals show ID
llm-cli tools approvals approve ID
llm-cli tools approvals decline ID
```

Aufrufe von KI-Clients, die dich nicht selbst fragen konnten (der Client hat keine Formular-Elicitation, die Frage ist
länger als 4.000 Zeichen, oder du hast das Tool mit `tools permissions --confirm-in-framework TOOL` gesetzt). `list`
(auch ohne Unterbefehl) zeigt jeden wartenden Aufruf mit ID, Tool, Aufruferklasse, dem selbst angegebenen Namen des
Clients und der verbleibenden Zeit; `show` gibt die vollständige Frage aus; `approve` und `decline` zeigen sie erneut
und fragen „Approve this call?“ oder „Decline this call?“, bevor etwas entschieden wird. Bei der Freigabe führt das
Framework den Aufruf für den Client aus, unter dem Aufrufer des Clients; der Client liest das Ergebnis mit
`get_job_status`. Jeder Unterbefehl braucht ein Terminal (sonst Exit-Code 2); eine Entscheidung wird abgelehnt
(Exit-Code 1), wenn die Freigabe verfallen ist (15 Minuten), schon entschieden wurde, die Sitzung ihres Clients
beendet ist, sich ihre Frage inzwischen geändert hat oder das Audit-Log die Entscheidung nicht festhalten kann. Auf
einem Rechner ohne Bildschirm, etwa im Docker-Image, führst du es dort in einem Terminal aus
(`docker exec -it <container> llm-cli tools approvals`). Die GUI zeigt dieselben Aufrufe unter
**Tools > Pending Approvals...**.

`approve ID --always [--width this|any] [--duration revoke|30d|24h]` gibt den Aufruf frei und speichert eine
dauerhafte Freigabe („nicht mehr fragen“) im Rahmen, den das Framework dafür angeboten hat, nachdem Rahmen und
Warnung gezeigt wurden; `list` kennzeichnet die Aufrufe, bei denen sie angeboten wird, und die Anfragen, die der
Dialog eines Clients geschickt hat („asks to stop asking“), die nur mit einem Rahmen freigegeben werden. `--always`
wird abgelehnt, solange eine GUI-Sitzung des Frameworks läuft: Nutze ihr Freigabefenster. Für alle lokalen
stdio-Clients zusammen gibt es nur `--width this` und `--duration 30d` oder `24h`.

| Option von `tools permissions` | Bedeutung |
|---|---|
| `--confirm-in-framework TOOL` | Fragen zu diesem Tool immer im Framework stellen (im Freigabefenster oder mit diesem Befehl), nie im eigenen Dialog des KI-Clients. Wiederholbar; mit `--caller` für einen Aufrufer. Braucht ein Terminal; wird festgehalten. |
| `--confirm-anywhere TOOL` | `--confirm-in-framework` für dieses Tool zurücknehmen. |
| `--grants` | Die dauerhaften Freigaben jedes Aufrufers auflisten: was jede abdeckt, ihren Zustand (active, expired, lapsed, outdated), wann und wie sie angelegt wurde, ihre Nutzungen und letzte Nutzung (aus dem Audit-Log) und ihren Ablauf. |
| `--revoke-grant ID` | Eine dauerhafte Freigabe sofort widerrufen (braucht ein Terminal; wird festgehalten). `--reset` löscht alle, `--reset --caller` die dieses Aufrufers; das Widerrufen eines Tokens entfernt seine eigenen. |

### tools folders

```
llm-cli tools folders list
llm-cli tools folders add PATH --label LABEL [--kind model|dataset|any]
llm-cli tools folders remove LABEL
```

Die Ordner, deren Modell- und Datensatzdateien Ditto und MCP-Clients nutzen dürfen. Sie nennen nie einen Pfad,
sondern eine Datei als `<Label>:<relativer Pfad>`, zum Beispiel `models:qwen2.5-0.5b-instruct` oder
`mymodels:llama/model.gguf`. Der Modellordner (`models`, Einstellung `models_dir`) und der Datensatzordner
(`datasets`, Einstellung `datasets_dir`) sind immer da; mit `add` dürfen sie einen weiteren Ordner unter einem Label
nutzen, das du wählst (Kleinbuchstaben, Ziffern, `-` und `_`).

- `add` und `remove` brauchen ein Terminal (weitergeleitete Antworten werden abgelehnt), und `add` zeigt den Ordner
  und fragt vorher: Dateien darin können in Builds eingebunden werden, die Community- oder KI-geschriebenen Code
  ausführen, und können über Build-Logs KI-Clients erreichen.
- `add` lehnt `/`, dein Home selbst, einen Ordner, der den persönlichen Ordner des Frameworks enthält, und einen
  Ordner in einem gesperrten Ordner oder mit einem gesperrten Ordner darin ab (Schlüssel, Anmeldedaten, das
  Hugging-Face-Token, Systemordner; die Liste steht in den [Funktionen](Features_DE.md#mcp-server)). Der Unterordner
  `hub` des Hugging-Face-Caches lässt sich hinzufügen (`~/.cache/huggingface/hub`, wo `hf download` Modelle
  ablegt); die Token-Dateien daneben bleiben gesperrt. Dieselben Grenzen gelten für `models_dir` und
  `datasets_dir`: Zeigt eine der Einstellungen auf `/`, dein Home oder einen Ordner mit dem persönlichen Ordner
  darin, markiert `list` das Label als nicht nutzbar.
- Der echte Pfad des Ordners wird festgehalten, und zwar der, den `add` dir gezeigt hat: Zeigt ein Link während der
  Frage woandershin, wird nichts hinzugefügt. Ändert er sich später, markiert `list` ihn als nicht nutzbar, und
  KI-Clients werden abgewiesen, bis du `add` mit demselben Label erneut ausführst.
- Jede Änderung landet im Audit-Log (Akteur `cli`; `gui` für dieselbe Liste in der GUI unter Tools > Folders the AI
  May Use...); lässt sich das Hinzufügen nicht festhalten, wird der Ordner nicht hinzugefügt. Die Liste liegt in
  `~/.llm-framework/ai_folders.json`, nur für dich lesbar.

```bash
llm-cli tools folders add ~/llm-models --label mymodels --kind model
llm-cli tools folders add ~/.cache/huggingface/hub --label hub
llm-cli tools folders list
llm-cli tools folders remove mymodels
```

### tools crawl-notice

```
llm-cli tools crawl-notice
```

Zeigt den einmaligen Deep-Ingest-Hinweis (was der Crawler liest, welche Opt-outs er beachtet, dass das Wissen auf
diesem Rechner bleibt) und fragt, ob du ihn gelesen hast. KI-Clients und Ditto können erst dann darum bitten,
Dokumentations-Websites zu crawlen (`crawl_docs_site`), wenn eine Person ihn gelesen hat, hier oder im Deep Ingest
der GUI; kein Tool kann ihn bestätigen. Der Befehl braucht ein Terminal (weitergeleitete Antworten werden
abgelehnt); die Bestätigung wird mit `via: cli` in `~/.llm-framework/crawl_notice.json` gespeichert und ins
Audit-Log geschrieben (Akteur `cli`). Ist der Hinweis schon gelesen, wird er gezeigt und nichts ändert sich; nach
einer Änderung seines Textes muss er erneut gelesen werden.

---

## mcp

### mcp serve

```
llm-cli mcp serve [--transport stdio|http] [--host HOST] [--port PORT] [--container]
```

Betreibt den MCP-Server. Normalerweise startet ihn der KI-Client für dich (siehe `mcp config`). stdout trägt das
Protokoll, Logs gehen nach stderr. Sind die Tool-Berechtigungen noch nicht eingerichtet, funktionieren nur lesende
Tools ohne Nachfrage, alles andere fragt (kann der Client nicht fragen, wartet die Frage im Framework: siehe
[tools approvals](#tools-approvals)).

| Option | Bedeutung |
|---|---|
| `--transport [stdio\|http]` | `stdio` (Standard): vom Client gestartet. `http`: lokales Streamable HTTP mit einem benannten Token je Client. |
| `--host TEXT` | Nur HTTP; nur Loopback-Adressen (Standard `127.0.0.1`; außerdem `::1`, `localhost`). |
| `--port INTEGER` | Nur HTTP (Standard 8765). |
| `--container` | Nur HTTP in einem Container (Docker, Podman): bindet die Schnittstellen des Containers statt Loopback; außerhalb eines Containers abgelehnt. Veröffentliche den Port nur auf dem Loopback des Hosts (`-p 127.0.0.1:8765:8765`); benanntes Token, `Host`- und `Origin`-Prüfung gelten wie ohne die Option. |

Mit `--transport http` lauscht der Server auf `http://<Host>:<Port>/mcp`, und jede Anfrage braucht
`Authorization: Bearer <Token>` mit einem [benannten Token](#mcp-token); der Server startet nicht, bevor es eins gibt,
und kein Token wird angenommen (und der Server startet nicht), solange andere Benutzer die Token-Datei lesen oder
ändern können oder sie einem anderen Benutzer gehört. Anfragen mit `Origin`-Header (Webseiten, CORS-Preflights)
werden abgelehnt, der `Host`-Header muss `127.0.0.1`, `localhost` oder `[::1]` mit dem Port des Servers sein, und der
Server hält höchstens 8 Sitzungen, davon höchstens 4 je Token (damit ein Client die anderen nicht aussperren kann;
darüber hinaus wird eine neue Sitzung mit Status 503 abgelehnt), schließt eine Sitzung nach 10 Minuten ohne Anfrage
und nimmt höchstens 1 MiB pro Anfrage an. Eine noch offene Anfrage (ein Warten, ein Ereignisstrom) wird geschlossen,
sobald ihr Token widerrufen oder rotiert wird, und ebenso die Sitzungen dieses Tokens (eine Sitzung gehört dem
Token-Wert, der sie geöffnet hat; nach `rotate` öffnet der Client eine neue). Die alte gemeinsame Token-Datei
`mcp_http_token` wird nicht mehr akzeptiert.

Über stdio macht die Umgebungsvariable `LCF_MCP_TOKEN` im Server-Eintrag des Clients den Client zum Aufrufer dieses
benannten Tokens (`mcp:<Name>`, mit den Berechtigungen des Tokens) statt zum gemeinsamen Aufrufer `mcp`. `llm-cli`
nimmt sie aus seiner Umgebung, bevor irgendein Befehl läuft, gleich welcher (kein Befehl und kein Kindprozess erbt
sie), und gibt sie nur an `mcp serve` weiter, das mit Exit-Code 2 endet, wenn der Wert kein gültiges Token ist; ein
laufender Server antwortet nicht mehr, sobald sein Token widerrufen oder rotiert wird.

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

### mcp token

```
llm-cli mcp token add NAME [--policy-from CALLER | --link ditto]
llm-cli mcp token list
llm-cli mcp token rotate NAME
llm-cli mcp token revoke NAME
```

Benannte Tokens, eins pro Client, der sich über HTTP oder über stdio mit `LCF_MCP_TOKEN` verbindet. Ein Token wird
einmal gezeigt, wenn es angelegt oder rotiert wird; das Framework behält nur seinen SHA-256 in
`~/.llm-framework/mcp_tokens.json` (Modus 0600). Sein Name (1-32 Kleinbuchstaben, Ziffern und Bindestriche) ist der
Aufrufer `mcp:<Name>` im Audit-Log, und es hat eigene Berechtigungen. `add`, `rotate` und `revoke` brauchen einen
Menschen an einem Terminal (`add` und `rotate` geben außerdem nur auf ein Terminal aus) und werden mit dem Akteur
`cli` protokolliert, ohne das Token. Kein Tool kann Tokens anlegen, auflisten, ändern oder lesen. Die GUI kann
dasselbe unter Tools > Tool Permissions (Ditto / MCP), Abschnitt „MCP tokens“ (protokolliert mit dem Akteur `gui`).

| Befehl / Option | Bedeutung |
|---|---|
| `add NAME` | Legt ein Token an, das nur lesen darf, bis du mit `tools permissions --token NAME` mehr erlaubst. |
| `--policy-from CALLER` | Beginnt mit einer Kopie der Berechtigungen eines anderen Aufrufers: `mcp` (lokale stdio-Clients), `ditto` oder ein Token-Name; `mcp:NAME` meint immer ein Token, auch eins namens `mcp` oder `ditto`. Spätere Änderungen der Quelle gelten nicht. |
| `--link ditto` | Nutzt Dittos Berechtigungen per Verweis, für Ditto auf einem anderen Rechner: Eine Änderung an Dittos Berechtigungen gilt sofort. |
| `list` | Namen, Audit-Akteure, Berechtigungen, Zeit des Anlegens und Rotierens; nie ein Token oder sein Hash. |
| `rotate NAME` | Ein neues Token für denselben Namen und dieselben Berechtigungen; das alte funktioniert sofort nicht mehr. |
| `revoke NAME` | Löscht das Token und seine Berechtigungen nach einer Rückfrage; sein Client verliert sofort den Zugang. |

```bash
llm-cli mcp token add laptop                          # nur lesen
llm-cli tools permissions --token laptop              # mehr erlauben
llm-cli mcp token add ditto-laptop --link ditto       # Ditto auf einem anderen Rechner
claude mcp add --transport http llm-cross-compiler http://127.0.0.1:8765/mcp --header "Authorization: Bearer <token>"
```

---

## secrets

API-Schlüssel und Tokens werden verschlüsselt im Tresor für Zugangsdaten `secrets.store` in deinem persönlichen
Statusordner gespeichert (`~/.llm-framework` oder `LLM_FRAMEWORK_HOME`). Der Tresor liegt nie in einer
Git-Arbeitskopie. Sein Schlüssel liegt standardmäßig im Schlüsselbund deines Betriebssystems. Ohne nutzbaren
Schlüsselbund brechen Befehle, die das Framework brauchen, mit Exit-Code 5 ab und nennen die Möglichkeiten; einen
anderen Schutz wählst du mit [`llm-cli ditto auth storage --use`](#ditto-auth-storage), mit der Einstellung
`secret_storage` in `config.yml` oder für einen einzelnen Lauf mit der Umgebungsvariable `LLM_SECRET_STORAGE`:

| `secret_storage` | Woher der Schlüssel des Tresors kommt |
|---|---|
| `keyring` | Aus dem Schlüsselbund des Betriebssystems (Standard). |
| `helper` | Vom Befehl in der Einstellung `secret_helper`, z. B. `pass show llmcc/vault-key`, ohne Shell ausgeführt. |
| `passphrase` | Aus einer Passphrase, die einmal je Start abgefragt wird (mindestens 8 Zeichen), nie aus einer Datei oder Variable. |
| `insecure` | Aus einer Schlüsseldatei neben dem Tresor: **im Ruhezustand nicht verschlüsselt**. |
| `ephemeral` | Nur im Arbeitsspeicher, nichts wird gespeichert (für CI). |

### secrets set

```
llm-cli secrets set KEY [--with-token] [--password TEXT]
```

Speichert ein Geheimnis unter dem Namen `KEY`. Der Wert wird zweimal ohne Anzeige abgefragt oder mit `--with-token`
von stdin gelesen; so bleibt er aus deinem Shell-Verlauf. Die Schlüssel für Dittos Anbieter speicherst du besser mit
[`llm-cli ditto login`](#ditto-login), das sie auch prüft.

| Option | Bedeutung |
|---|---|
| `--with-token` | Den Wert von stdin lesen, z. B. `printf %s "$HF_TOKEN" \| llm-cli secrets set hf_token --with-token`. |
| `--password TEXT` | Veraltet: Der Wert landet in deinem Shell-Verlauf und in der Prozessliste. |

Namen, die das Framework verwendet:

| Name | Wofür |
|---|---|
| `anthropic_api_key`, `openai_api_key`, `gemini_api_key`, `openrouter_api_key`, `custom_api_key` | Dittos Anbieter (besser: `llm-cli ditto login <anbieter>`) |
| `hf_token` | Zugangsbeschränkte Hugging-Face-Modelle in Builds und die Hugging-Face-Tools der KI-Clients (`get_hf_model_info`, `download_hf_file`): nur im Framework gelesen, nie an eine KI weitergegeben; ohne es senden sie gar kein Token (nie eins aus `HF_TOKEN` oder dem Hugging-Face-Home-Ordner) |
| `qdrant_api_key` | Ein entfernter Qdrant-Server für die Wissensbasis (Einstellung `qdrant_url`) |
| `community_signing_key`, `community_age_identity` | Deine Schlüssel für Community-Pakete, beim ersten Mal von [`llm-cli zoo`](#zoo-keys) oder vom Community-Export und -Import der GUI angelegt; nicht von Hand setzen |

```bash
llm-cli secrets set hf_token
printf %s "$GITHUB_TOKEN" | llm-cli secrets set github_token --with-token
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
llm-cli module create [--name TEXT] [--arch aarch64|x86_64|armv7l|riscv64] [--sdk TEXT] [--probe DATEI]
```

Legt ein neues Target per Fragen an: Name, Architektur und SDK (wenn nicht als Option angegeben), dann
Docker-Basis-Image (Standard `debian:bookworm-slim`), Beschreibung, Systempakete (Standard
`build-essential, cmake, git`) und CPU-Flags, die das Modul nur als Hinweis vermerkt und nie anwendet. Jeder Wert wird geprüft, bevor Dateien geschrieben werden, der
fertige Ordner muss die Target-Prüfungen bestehen
([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#91-was-die-prüfungen-verlangen)), und ein vorhandenes
Target wird nie überschrieben.

| Option | Bedeutung |
|---|---|
| `--name TEXT` | Name des neuen Targets. |
| `--arch [aarch64\|x86_64\|armv7l\|riscv64]` | CPU-Architektur. |
| `--sdk TEXT` | Besonderes SDK, z. B. `CUDA`, `RKNN` (Standard `None`). |
| `--probe DATEI` | Hardware-Probe des Targets (das Profil, das [`scan --device`](#scan) schreibt). Der Block `runtime:` der neuen `target.yml` ist dann der Plan des Build-Rezepts für die llama.cpp-Laufzeit des Boards daraus; eine Datei, die kein Probe-Profil ist, wird abgelehnt, bevor etwas geschrieben wird. |

Das Target wird in den eingestellten Targets-Ordner geschrieben (Einstellung `targets_dir`; ein relativer Pfad
gilt relativ zur Installation, nicht zum aktuellen Ordner). Seine `target.yml` hat einen Block `capabilities:` (Unterstützungsstufe `community`) mit dem Modulnamen als einzigem
Board und GGUF als einzigem Format; trag dort deine echten Boards und Formate ein. Die nächsten Schritte des
Befehls nennen `modules/build.sh`, den Dispatcher, den die Build-Engine ausführt; die erzeugte
`config_module.sh` und die übrigen `*_module.sh`-Skripte sind Altlasten, die die Build-Pipeline nicht aufruft
([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#10-altlasten)).

### module generate-ai

```
llm-cli module generate-ai PROBE_FILE [--provider TEXT] [--model TEXT]
```

Ditto schlägt ein Target-Modul aus einer Hardware-Probe-Datei vor (`target_hardware_config.txt`, siehe
[scan](#scan)). Der ganze Vorschlag wird als JSON ausgegeben; erst nach deiner Bestätigung wird das Modul im
eingestellten Targets-Ordner angelegt (und vom Modulgenerator erneut geprüft). Ditto nutzt die gespeicherte
Anmeldung des Anbieters ([`llm-cli ditto login`](#ditto-login)); der Befehl fragt selbst nie nach einem Schlüssel.
Weder `module create` noch `module generate-ai` hängt Seiten aus der Wissensbasis an; das ist eine Option auf der
letzten Seite des GUI-Assistenten. Ist die Probe-Datei ein Probe-Profil, wird der Block `runtime:` des Moduls daraus
geplant; die CPU- und CMake-Flags des Vorschlags stehen dort als Hinweistext und werden nie angewendet.

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
llm-cli scan [--output TEXT] [--json] [--script]
llm-cli scan --device HOST --user TEXT [--port N] [--password TEXT] [--trust-fingerprint TEXT] [--label TEXT]
             [--output TEXT] [--json]
llm-cli scan --import DATEI [--label TEXT]
```

Führt die Hardware-Probe aus und behält ihr Ergebnis, das Hardware-Profil (Schema 2, beschrieben im
[Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#11-die-datei-der-hardware-probe)). Ein Build wird für
den Rechner geplant, auf dem das Modell läuft, deshalb läuft die Probe auf diesem Rechner.

- **Dieser Rechner** (Standard): Die Python-Probe `scripts/hardware_probe.py` läuft in der CLI. Das Profil wird in die
  mit `--output` angegebene Datei geschrieben und als Profil dieses Rechners gespeichert
  (`~/.llm-framework/hardware/local.txt`, ohne flüchtige Werte wie den freien RAM). Kann die Python-Probe nicht
  laufen, sagt die CLI, warum, und führt stattdessen das Ersatzskript aus; `--script` nimmt das Skript direkt
  (`scripts/hardware_probe.sh` mit `sh` unter Linux, also auch auf Rechnern ohne bash wie Alpine oder BusyBox;
  `scripts/hardware_probe.ps1` unter Windows).
- **Ein Board per SSH** (`--device`): Beim ersten Kontakt zeigt die CLI den Fingerabdruck des SSH-Hostschlüssels und
  bittet dich, ihn zu bestätigen, wie bei `deploy run` (`--trust-fingerprint` bestätigt ihn ohne Nachfrage); ein
  Passwort wird einmal genutzt, um den SSH-Schlüssel des Frameworks zu installieren, und nicht gespeichert. Dann
  schickt die CLI `hardware_probe.py` an `python3 -I`, wenn das Board Python 3.8 oder neuer hat, sonst
  `hardware_probe.sh` an `sh` (jede POSIX-Shell, auch BusyBox). Das Skript kommt über den SSH-Kanal, also wird auf dem
  Board nichts installiert und nichts zurückgelassen. Das Ergebnis wird wie jede Probe-Datei von außen geprüft, nach
  `--output` geschrieben und unter der ID des Boards gespeichert (`~/.llm-framework/hardware/devices/<ID>.txt`; die ID
  kommt aus dem Hostschlüssel des Boards), mit deiner Bezeichnung und seiner Adresse. Der SSH-Benutzer wird zum
  Deploy-Benutzer des Boards für [`device runtime deploy`](#device), wenn noch keiner gesetzt ist (nie root).
- **Die Probe-Datei eines Boards** (`--import DATEI`): Eine `target_hardware_config.txt`, die du von einem Board
  kopiert hast (dort von `scripts/hardware_probe.sh` oder `llm-cli scan` geschrieben), wird wie jede Probe-Datei von
  außen geprüft, unter einer neuen zufälligen ID mit deiner Bezeichnung gespeichert (Standard: das Board-Modell aus
  der Datei), und die CLI zeigt diese **Probe-ID** und das passende Target (über den SoC als Board eines Targets,
  dann über den Hersteller von SoC oder NPU, bei gleicher Architektur). Eine ältere Datei nach Schema 1 wird
  umgewandelt; was das Schema nicht kennt, darunter der Hostname, fällt weg. KI-Clients und Ditto nennen die Probe
  eines Boards nur über diese ID (`get_hardware_probe`, der Prompt `add_board`); kein Tool liest eine Probe über
  einen Pfad.

Auf diesem Rechner läuft die Probe ohne Terminal: Das Ersatzskript bekommt seine Eingabe aus `/dev/null`,
`NON_INTERACTIVE=true` und einen eigenen Arbeitsordner (`~/.llm-framework/hardware/work`, nie den aktuellen Ordner),
und eine Probe, die länger als 120 Sekunden braucht, wird beendet. Derselbe Dienst führt die Probe für das MCP-Tool
`probe_local_hardware` aus.

Die Probes fragen nie nach, installieren nichts und verlangen keine Administratorrechte. Exit-Code 1, wenn die Probe
scheitert oder ihre Datei nicht lesbar ist, 2 bei falschen Optionen.

| Option | Bedeutung |
|---|---|
| `--output TEXT` | Datei, in die das Profil geschrieben wird, relativ zum aktuellen Ordner (Standard `target_hardware_config.txt`); ihr Ordner wird angelegt. Mit `--script` gibt die CLI sie an das Skript weiter (`hardware_probe.sh --output DATEI`, `hardware_probe.ps1 -OutputFile DATEI`). |
| `--json` | Die JSON-Ansicht schreiben (dieselben Schlüssel und Werte als ein Objekt) statt `KEY=VALUE`-Zeilen. |
| `--script` | Das Ersatzskript statt der Python-Probe nehmen (nur für diesen Rechner). |
| `--device HOST` | Dieses Board per SSH untersuchen (Hostname oder IP-Adresse) statt dieses Rechners. |
| `--user TEXT` | SSH-Benutzer auf dem Board (nötig mit `--device`). |
| `--port N` | SSH-Port des Boards (Standard 22). |
| `--password TEXT` | Nur für die erste Verbindung: wird einmal genutzt, um den SSH-Schlüssel des Frameworks zu installieren, und nie gespeichert (wird abgefragt, wenn es fehlt). |
| `--trust-fingerprint TEXT` | Einem neuen Board ohne Nachfrage vertrauen, wenn sein Hostschlüssel diesen SHA256-Fingerabdruck hat. |
| `--label TEXT` | Deine Bezeichnung für das Board im Profilspeicher (mit `--device` oder `--import`). |
| `--import DATEI` | Die Probe-Datei eines Boards im Probe-Speicher ablegen und ihre Probe-ID zeigen (nicht zusammen mit `--device`). |

Ein Rechner ohne Netzzugang führt eine Probe von Hand aus, und du importierst ihre Datei
([README](../README_DE.md#die-hardware-des-boards-untersuchen)). In der GUI importiert **Scan / Import...** eine
Probe-Datei, untersucht ein Board per SSH (mit derselben entfernten Probe, also auch auf Boards, die `/tmp` mit
`noexec` einhängen) oder diesen PC.

```bash
llm-cli scan                                        # dieser Rechner, gespeichert als „local“
llm-cli scan --device 192.0.2.10 --user dietpi --label "ZERO 3W" --output zero3w.txt
llm-cli scan --import zero3w.txt --label "ZERO 3W"   # eine von einem Board kopierte Probe-Datei
```

---

## device

Die Boards, die du mit `scan --device` untersucht hast, und die selbst gebaute llama.cpp-Laufzeit darauf
([Funktionen](Features_DE.md#laufzeit-auf-dem-board-selbst-gebautes-llamacpp)). `GERÄT` ist die ID eines Boards
(oder ihre ersten 4 oder mehr Zeichen), seine Bezeichnung oder seine gespeicherte Adresse, wie `device list` sie
zeigt. Der Build braucht Docker auf deinem PC; das Board braucht SSH, `tar`, `gzip` und `sha256sum` (BusyBox hat
sie) und einen unprivilegierten Benutzer.

```
llm-cli device list [--json]
llm-cli device settings GERÄT [--install user|systemd] [--user NAME] [--port N] [--model PFAD] [--confirm-sudo]
llm-cli device runtime plan GERÄT [--json]
llm-cli device runtime build GERÄT [--jobs N] [--mirror URL] [--yes]
llm-cli device runtime deploy GERÄT [--user NAME] [--password TEXT] [--bench-model PFAD] [--allow-untrusted-model]
llm-cli device runtime verify GERÄT [--user NAME] [--bench-model PFAD] [--allow-untrusted-model]
llm-cli device runtime status GERÄT [--json]
```

### device list

Die gespeicherten Boards: ID, Bezeichnung, Adresse, System (OS, Version, Architektur, C-Bibliothek mit Version,
Init-System), Deploy-Einstellungen und die deployte Laufzeit. `--json` für Skripte.

### device settings

Zeigt oder ändert, wie die Laufzeit auf dem Board installiert wird. Das wird hier entschieden, vor einem Deployment;
ein Deployment fragt nie.

| Option | Bedeutung |
|---|---|
| `--install user` | Das Startskript in deinem Home auf dem Board (die Vorgabe). Mit ihm startest du den Server. |
| `--install systemd` | Zusätzlich eine systemd-Unit, die den Server beim Booten startet. Nur für Boards, deren Probe `INIT_SYSTEM=systemd` meldet; sie braucht `--model` und deine Bestätigung, gleich hier, dass Deployments auf dem Board `sudo -n` ausführen dürfen (am Terminal abgefragt oder `--confirm-sudo`; wird ins Audit-Log geschrieben). Wählst du wieder `user`, verfällt die Bestätigung. |
| `--user NAME` | Der SSH-Benutzer auf dem Board; der Server und jede Prüfung laufen als dieser Benutzer. root wird abgelehnt. |
| `--port N` | Der Loopback-Port des Servers der systemd-Unit (1024-65535, Vorgabe 8080). Das Startskript lauscht auf 8080, solange es kein `--port` bekommt; der Startbefehl, den ein Deployment ausgibt, enthält diesen Port. |
| `--model PFAD` | Die Modelldatei auf dem Board, die die systemd-Unit bereitstellt (relative Pfade liegen im Home des Benutzers). |
| `--confirm-sudo` | Mit `--install systemd`: ohne Nachfrage bestätigen. |

Exit-Code 1, wenn das Board kein systemd hat oder die Bestätigung fehlt (dann wird nichts geändert), 2 bei einem
falschen Wert.

### device runtime plan

Zeigt den Cross-Build, der aus dem eigenen Profil des Boards geplant wird: Architektur, C-Bibliothek mit Version,
die Builder-Basis mit ihrem GCC, `GGML_CPU_ARM_ARCH` oder die x86-Merkmale, OpenMP und `libgomp.so.1`, die
Programme, die Build-ID und ob er gebaut ist. `--json` gibt den ganzen Bauplan aus. Exit-Code 1, wenn sich kein
Bauplan machen lässt (zum Beispiel für ein Board, dessen glibc älter ist als jede Builder-Basis, oder ein Profil ohne
`LIBC_VERSION`: untersuche es erneut).

### device runtime build

Holt den festgelegten llama.cpp-Quelltext, wenn er nicht im Cache liegt (es fragt vorher; `--yes` fragt nicht,
`--mirror` holt von einem Spiegel, `ditto runtime import-source` bringt ihn auf einen Rechner ohne Internet), baut
den Bauplan im Builder-Container ohne Netz (`--jobs` setzt die parallelen Jobs; ohne richten sie sich nach dem
Speicher), prüft das Format der Programme auf deinem PC und packt den Tarball nach
`~/.llm-framework/runtime/llama.cpp/device/<ID>/llama-runtime-<Arch>-<Build-ID>.tar.gz`. Ein schon abgelegter Build
wird nur neu gepackt. Exit-Code 1, wenn Docker nicht antwortet, der Build scheitert oder seine Prüfungen scheitern.

### device runtime deploy

Installiert die gebaute Laufzeit auf dem Board, wie die Deploy-Einstellungen es sagen, und prüft sie dort. Das Board
muss das sein, mit dem das Profil erstellt wurde (derselbe Hostschlüssel; ein neues Board unter der alten Adresse
wird abgelehnt), und ihm muss schon vertraut werden; `--password` koppelt ein Board, das noch keinen
Framework-Schlüssel hat (einmal genutzt, nie gespeichert). Der SSH-Benutzer kommt aus den Deploy-Einstellungen oder
aus `--user`.

1. Das Board wird untersucht: Benutzer-ID (root wird abgelehnt), Home-Ordner, Architektur (sie muss zum Build
   passen), Init-System und die Werkzeuge, die das Deployment braucht. Mit der systemd-Einstellung muss `sudo -n
   true` funktionieren; sonst wird nichts geändert. Mit `--bench-model PFAD` wird das Modell auf dem Board an seiner
   sha256 erkannt: ein Katalogmodell läuft sofort, jedes andere nur, wenn du zustimmst (am Terminal oder
   `--allow-untrusted-model`), und das wird ins Audit-Log geschrieben. Das wird hier gefragt, bevor sich auf dem
   Board etwas ändert.
2. Der Tarball kommt über den SSH-Kanal in einen privaten Staging-Ordner in
   `~/.local/share/llm-framework/llama.cpp/`, wird gegen seine sha256 und jede Datei gegen `SHA256SUMS` geprüft, nur
   für dich lesbar gemacht und neben den aktiven Build gelegt.
3. Das Startskript erzeugt den Schlüssel `~/.config/llm-framework/llama-server.key` auf dem Board (umask 077).
4. Der neue Build wird in seinem eigenen Ordner geprüft, als dein Benutzer: `--version` gegen den Pin,
   `--list-devices`, und `llama-server` muss `--tools` ablehnen; mit `--bench-model` auch ein kurzer
   `llama-bench`-Lauf mit diesem Modell.
5. Nur ein Build, der besteht, wird zu `current`. Ein Build, der scheitert (zum Beispiel mit `SIGILL`, weil die
   CPU-Grundlage nicht zum Board passt), wird wieder entfernt, der vorige Build bleibt aktiv, und sonst ändert sich
   nichts. Nach dem Umschalten bleibt der vorige Build; ältere werden entfernt, außer ein laufendes Programm nutzt
   sie.
6. Mit der systemd-Einstellung wird die Unit `llm-llama-server.service` mit `sudo -n` geschrieben und aktiviert und
   gestartet, sobald das Modell da ist.

Das Ergebnis liegt in `device/<ID>/deployed.json` (ein gescheitertes Deployment ändert es nicht). Exit-Code 1, wenn
ein Schritt oder eine Prüfung scheitert.

Auf dem Board startet das Startskript den Server:

```bash
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh start ~/models/model.gguf -c 4096 -t 4
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh status
~/.local/share/llm-framework/llama.cpp/current/llama-server.sh stop
curl -s http://127.0.0.1:8080/health   # braucht keinen Schlüssel; jede andere Anfrage braucht "Authorization: Bearer <Schlüssel>"
```

Es lauscht auf 127.0.0.1 (Vorgabe-Port 8080, `--port` ab 1024) mit der Schlüsseldatei und einer geleerten Umgebung.
Es reicht `-c`, `-t`, `-tb`, `-C`, `--cpu-strict`, `-b`, `-ub`, `-np`, `-ngl`, `-fa`, `-cram`, `-lm`, `-a` und
`--sleep-idle-seconds` mit geprüften Werten weiter und lehnt jede andere Option ab (Exit-Code 2). `serve` läuft im
Vordergrund, `keygen` erzeugt den Schlüssel, `version` gibt die Version des Servers aus.

### device runtime verify

Führt die Prüfungen aus Schritt 4 erneut für den aktiven Build `current` aus (auch mit `--bench-model`) und
aktualisiert den Eintrag.

### device runtime status

Was deployt ist (Build, Zeit, Startskript oder systemd, Ergebnis der Prüfungen) und ob es noch der Build ist, den
das Profil des Boards, der Pin und das Rezept verlangen; wenn nicht, erneut bauen und deployen.

Für ein [vertrautes Gerät](#deploy-devices) führen die optionalen MCP-Tools `probe_remote_device`,
`deploy_device_runtime` und `run_device_benchmark` für einen KI-Client `scan --device`, `device runtime build` mit
`deploy` und `device runtime verify --bench-model` aus, jedes von dir bestätigt; die Deploy-Einstellungen bleiben
immer deine (`device settings`).

```bash
llm-cli scan --device 192.0.2.10 --user dietpi --label "ZERO 3W"
llm-cli device runtime plan "ZERO 3W"
llm-cli device runtime build "ZERO 3W" --yes
llm-cli device runtime deploy "ZERO 3W" --bench-model models/qwen3.5-2b-q4_0.gguf
# Die systemd-Unit statt des Startskripts, vor dem nächsten Deployment entschieden
llm-cli device settings "ZERO 3W" --install systemd --model models/qwen3.5-2b-q4_0.gguf
```

---

## config

### config show

```
llm-cli config show
```

Gibt die wirksamen Einstellungen als JSON aus, so wie das Framework sie liest: jede Einstellung mit ihrem Wert
(`value`) und ihrer Herkunft (`source`: `default`, `file <Pfad>` für die `config.yml` oder eine mit `-c` angegebene
Datei, `environment` für eine Variable `LLM_FRAMEWORK_<KEY>`). Zugangsdaten werden geschwärzt: Tokens, Schlüssel und
Passwörter in Werten (zum Beispiel in einer URL) und jeder Wert unter einem Namen, der nach Zugangsdaten aussieht,
ob eine unbekannte Einstellung (`hf_token`) oder ein Schlüssel innerhalb einer Einstellung (`password`, `api_key`).
Der Befehl braucht weder den Tresor für Zugangsdaten noch Docker.

```bash
llm-cli -q config show | jq '.models_dir'
```

KI-Clients lesen Einstellungen mit dem MCP-Tool `get_settings` (eine feste Liste; Adressen als Schema und Host,
Ordner als Labels) und ändern gewöhnliche mit `update_settings`, das dich mit altem und neuem Wert fragt und die
Änderung in der `config.yml` speichert. Einstellungen, die begrenzen, was eine KI darf, lassen sich so nur
verschärfen; Berechtigungen, Geheimnisse, den Tresor, Einstellungen des KI-Anbieters, Adressen und Ordner änderst
nur du, hier oder in der GUI.

### config repos

```
llm-cli config repos [--add NAME URL] [--remove NAME]
```

Ohne Optionen listet es die Überschreibungen für Quell-Repositorys auf (Einstellung `source_repositories`). `--add`
und `--remove` ändern sie und speichern die `config.yml`; lässt sie sich nicht speichern, sagt der Befehl das und
endet mit Code 1. `--add` speichert einen Eintrag `{url: ...}`; die Build-Engine reicht jeden Eintrag, auch
einfache URLs aus früheren Versionen, als `<NAME>_REPO_OVERRIDE` an die Stufen-Container weiter (`NAME`: der ganze
Name in Großbuchstaben, jedes Zeichen außer ASCII-Buchstaben und Ziffern als `_`, also ergeben `llama.cpp`,
`llama_cpp` und `llama-cpp` alle `LLAMA_CPP_REPO_OVERRIDE`). Ein Name mit Punkt wird zusätzlich unter dem Namen
früherer Versionen weitergereicht, seinem Teil nach dem letzten Punkt (`sources.llama_cpp` ergibt auch
`LLAMA_CPP_REPO_OVERRIDE`). `--add` lehnt einen Namen ohne ASCII-Buchstaben und Ziffern ab (Exit-Code 2).

| Option | Bedeutung |
|---|---|
| `--add TEXT...` | Eine Überschreibung hinzufügen: `NAME URL`. |
| `--remove TEXT` | Die Überschreibung `NAME` entfernen. |

Die aktuellen Rockchip-Skripte lesen keine dieser Variablen (nur das Altlast-Skript `source_module.sh` liest
`LLAMA_CPP_REPO_OVERRIDE`), daher wirken sich die Überschreibungen auf Rockchip-Builds nicht aus.

---

## zoo

Der Community-Zoo: Hardware-Targets, Rezepte und Wissenspakete, die andere Nutzer teilen. Ein Rezept ist eine
„Modellvorlage“: ein Modell in einem festgelegten Commit, gebaut für ein Target, Board, Format und eine
Quantisierung, mit den Prüfsummen dessen, was herauskam. Das Framework lädt nie von sich aus etwas hoch; Teilen ist
immer ein Schritt, den du anstößt.

**Aktueller Stand:** Das Community-Repository und sein signierter Index sind noch nicht veröffentlicht. Die
Einstellungen `community_index_url` und `community_repo` sind leer, und `configs/community_keys.yml` enthält keinen
Herausgeber-Schlüssel. Bis dahin zeigt `zoo list` nur installierte Pakete, Installieren per ID und
`zoo export --pr` brechen mit einer Meldung ab, und Pakete werden als Dateien geteilt (`zoo install --file`).

KI-Clients und Ditto nutzen denselben Zoo über die Tools `list_packs`, `add_pack_from_index` (nur signierter Index,
immer gefragt), `start_recipe_build` (ein installiertes Rezept, genau wie festgelegt, mit der Frage von
`start_build`) und `export_pack` (in eine lokale Datei, immer gefragt); sie können kein Paket aus einer Datei
installieren, keinen Pull Request öffnen und deine Schlüssel nicht anlegen
([Details](Features_DE.md#packs-für-ki-clients)).

So sind Pakete geschützt:

- Ein Paket ist eine ZIP-Datei mit einem Manifest (`pack.json`: sha256, Größe und Modus jeder Datei) und meist einer
  Ed25519-Signatur seines Autors (`pack.sig`). Mit diesem Framework exportierte Pakete haben immer eine; bei einem
  Paket ohne Signatur sagt die Zusammenfassung „not signed by its author“. Ein Paket enthält Skripte, Rezepte und
  Text, nie Modellgewichte oder andere Binärdateien.
- Die Liste der Pakete kommt aus einem signierten Index: `index.json` unter `community_index_url` und
  `index.json.sig` daneben, geprüft mit den Schlüsseln in `configs/community_keys.yml`, die jeweils bis zu ihrem
  Ablaufdatum gelten. Ein Index, der die Prüfung nicht besteht, wird nicht verwendet. Der letzte gültige Index wird
  in `~/.llm-framework/community/` zwischengespeichert, neu geholt, wenn er älter als einen Tag ist, und nie durch
  einen älteren ersetzt.
- Installieren per ID liest zuerst den aktuellen Index, damit kein Rückzug übersehen wird, und lädt das Paket
  festgelegt über Größe und sha256 herunter. Jedes Paket wird in einen Quarantäne-Ordner entpackt und dort geprüft:
  genau die aufgeführten Dateien mit ihren Prüfsummen, keine Links, die Target-Prüfungen
  ([Leitfaden für Modulentwickler](Module_Developer_Guide_DE.md#91-was-die-prüfungen-verlangen)) und die Formate
  von Rezepten und Wissen. Erst wenn du die Zusammenfassung bestätigst, wird es an seinen Platz verschoben, mit einer
  Herkunftsdatei `.lcf-provenance.json`.
- Autorensignaturen zeigen, wer ein Paket gemacht hat. Der erste Schlüssel, der für einen Autorennamen auftaucht,
  wird gemerkt; ein späteres Paket mit diesem Namen, aber einem anderen Schlüssel, wird mit einer Warnung gezeigt.

Vertrauensstufen:

| Stufe | Bedeutung |
|---|---|
| `verified` | Im signierten Index: geprüft und unabhängig nachgebaut. |
| `community` | Im signierten Index: geprüft. |
| `local` | Eine Datei, die der signierte Index nicht aufführt. Niemand hat sie geprüft: Die Installation fragt immer nach, und jeder Build eines solchen Targets zeigt eine Warnung. |
| `revoked` | In der Liste zurückgezogener Pakete des Index. Es wird nicht installiert; installierte Kopien werden von `zoo list`, `zoo verify`, `llm-cli doctor` und dem Community Hub markiert und nicht gebaut, auch nachdem der Schlüssel abgelaufen ist, der den Rückzug signiert hat. |

Targets, die mit dem Framework ausgeliefert werden, etwa Rockchip, haben keine Herkunftsdatei und erscheinen nicht im
Zoo.

Installierte Targets landen im Targets-Ordner, Rezepte in `community/recipes/<ID>/` und Wissenspakete in
`community/knowledge/<ID>/` der Installation. Ein Wissenspaket wird direkt nach der Installation in deine
Wissensbasis importiert oder, wenn die Wissensbasis ausgeschaltet ist, sobald sie eingeschaltet wird.

In der GUI: **Tools > Community Hub öffnen** (Liste, Filter nach Art und Vertrauensstufe, „Import file...“, „Build“
bei einem installierten Rezept), die letzte Seite des Modul-Assistenten und das Kontextmenü des Builder-Logs
(„Export a finished build for the community...“).

### zoo list

```
llm-cli zoo list [--kind target|recipe|knowledge] [--refresh] [--json]
```

Listet die Einträge des signierten Index (jeweils die neueste Version) und die installierten Pakete auf: Art, ID,
Version, Vertrauensstufe, installierte Version, Autor und Titel. Unter der Tabelle steht, welcher Index verwendet
wurde oder warum es keinen gibt (zum Beispiel „No community index: No community index is configured (setting
community_index_url).“); installierte Pakete werden in jedem Fall aufgeführt.

| Option | Bedeutung |
|---|---|
| `--kind [target\|recipe\|knowledge]` | Nur diese Art. |
| `--refresh` | Den signierten Index erneut herunterladen, auch wenn der zwischengespeicherte jünger als einen Tag ist. Ohne Netzwerk wird der zwischengespeicherte Index verwendet. |
| `--json` | Maschinenlesbare Ausgabe: eine Liste mit `kind`, `id`, `version`, `trust`, `installed_version`, `revoked`, `author` und `title`. |

### zoo show

```
llm-cli zoo show PACK_ID [--kind target|recipe|knowledge]
```

Zeigt einen Eintrag: Art, ID, Version, Autor und Lizenz, Vertrauensstufe (und warum es zurückgezogen wurde), wo es
installiert ist, die Download-Adresse mit Größe und sha256, die Beschreibung und bei einem installierten Rezept
dessen `recipe.yml`. Groß- und Kleinschreibung der ID spielt keine Rolle. Der Befehl liest nur den
zwischengespeicherten Index und die installierten Pakete; führe zuerst `zoo list --refresh` aus, um neue Einträge zu
sehen. Eine unbekannte ID endet mit Exit-Code 1.

| Option | Bedeutung |
|---|---|
| `--kind [target\|recipe\|knowledge]` | Die Art, wenn es die ID für mehrere Arten gibt. |

### zoo install

```
llm-cli zoo install PACK_ID [--kind target|recipe|knowledge] [--yes]
llm-cli zoo install --file FILE [--passphrase] [--from SCHLÜSSELDATEI ...] [--yes]
```

Installiert ein Paket aus dem signierten Index (per ID) oder aus einer Datei. Gib genau eines von beiden an (sonst
Exit-Code 2). Der Befehl gibt eine Zusammenfassung auf stderr aus und fragt „Install this pack?“ (Standard nein). So
sieht eine Zusammenfassung aus:

```
target my-board 1.0.0 by <author> (licence MIT)
Trust: community
Source: https://<address of the pack>
Files: 14 (48213 bytes)
Installs to: /opt/llm-conversion-framework/targets/my-board
Note: signed by author key 0123456789abcdef (first pack seen from this author)
```

| Argument / Option | Bedeutung |
|---|---|
| `PACK_ID` | Eine ID aus dem Index (`zoo list`). Installiert wird die neueste Version. |
| `--kind [target\|recipe\|knowledge]` | Art von `PACK_ID` (Standard `target`). Eine Paketdatei nennt ihre Art selbst. |
| `--file FILE` | Eine Paketdatei installieren, die dir jemand gegeben hat, oder ein verschlüsseltes Paket (`.lcfpack`). Ihre Vertrauensstufe ist `local`, außer der signierte Index führt ihre sha256 auf. Funktioniert ohne Netzwerk. |
| `--from FILE` | Verschlüsselte Pakete: nur von diesen Absendern annehmen. Die Datei enthält die öffentlichen Schlüssel der Absender, wie `llm-cli zoo keys` sie ausgibt; wiederholbar. Ohne die Option wird jeder Absender angenommen; die Zusammenfassung nennt immer die Schlüssel-ID des Absenders. Bei einem Paket, das nicht verschlüsselt ist, bricht der Befehl mit Exit-Code 2 ab (ein einfaches Paket hat keinen Absender; seine Zusammenfassung zeigt die Signatur des Autors). |
| `--passphrase` | Verschlüsselte Pakete: nach der Passphrase fragen, statt das Paket mit deinem eigenen Schlüssel zu öffnen. |
| `--yes` | Bei einem Paket, dessen Vertrauensstufe aus dem signierten Index kommt, nicht nachfragen, auch bei einer Datei, die der Index führt. Eine Datei ohne Eintrag im Index ist `local` und wird immer bestätigt; ohne Terminal wird sie nicht installiert. |

Verhalten:

- Installieren per ID braucht das Netzwerk: Zuerst wird der Index neu heruntergeladen, und scheitert das, wird nichts
  installiert. Ein zurückgezogenes Paket wird abgelehnt.
- Ein verschlüsseltes Paket (eine age-Datei und die Signatur ihres Absenders, siehe
  [Funktionen](Features_DE.md#verschlüsselte-packs-zum-privaten-teilen)) wird vor dem Entschlüsseln gegen die
  Signatur geprüft. Es öffnet sich mit deiner eigenen age-Identität (siehe [zoo keys](#zoo-keys)) oder mit
  `--passphrase` mit der Passphrase; eine Passphrase mit einem scrypt-Arbeitsfaktor über 2^20 wird abgelehnt. Ein
  Paket mit Passphrase, das ohne `--passphrase` geöffnet wird, sagt das; deine eigene Identität wird nur für ein an
  Schlüssel verschlüsseltes Paket gelesen (und beim ersten Mal angelegt). Der Community Hub öffnet Pakete, die an
  deinen Schlüssel verschlüsselt sind; Pakete mit Passphrase brauchen diesen Befehl.
- Nichts wird über einen vorhandenen Ordner gleichen Namens installiert, auch wenn sich nur die Groß- und
  Kleinschreibung unterscheidet; entferne den alten Ordner zuerst.
- Exit-Code 1: Eine Prüfung ist gescheitert (die Probleme werden aufgeführt und der Quarantäne-Ordner wird gelöscht),
  du hast mit Nein geantwortet, oder es gibt kein Terminal für ein Paket, das die Frage braucht.

```bash
llm-cli zoo install my-board
llm-cli zoo install smollm2-135m-instruct-rk3588-gguf-q4_k_m --kind recipe
llm-cli zoo install --file contribution-target-my-board-1.0.0.zip
llm-cli zoo install --file target-my-board-1.0.0.lcfpack --from kollegin.keys
llm-cli zoo install --file target-my-board-1.0.0.lcfpack --passphrase
```

Ein Rezept braucht sein Target: Installiere zuerst das Target, damit das Rezept auch gegen die Capabilities des
Targets geprüft wird (ohne das Target vermerkt die Zusammenfassung nur, dass es fehlt). „Build“ bei einem
installierten Rezept im Community Hub füllt den Builder aus, einschließlich des festgelegten Commits des Modells.

### zoo verify

```
llm-cli zoo verify [PACK_FILE]
```

Ohne Argument prüft es jedes installierte Community-Paket gegen den zwischengespeicherten Index, ohne etwas
herunterzuladen: zurückgezogen oder seit der Installation verändert. Es gibt eine Zeile pro Paket aus (✔, oder ✘ mit
dem Grund) oder „No community packs installed.“. Exit-Code 1, wenn ein Paket ein Problem hat, sonst 0.

Mit `PACK_FILE` prüft es eine Paketdatei, ohne sie zu installieren: Format, Dateiliste und Prüfsummen, die Prüfungen
ihrer Art, eine enthaltene Autorensignatur und dass die Datenschutz-Bereinigung nichts darin ändern würde. Es gibt
„acceptable“ oder jedes Problem aus (Exit-Code 1). Diese Form funktioniert ohne Schlüsselbund; die CI des
Community-Repositorys führt sie für jedes eingereichte Paket aus.

```bash
llm-cli zoo verify
llm-cli zoo verify community/contributions/contribution-target-my-board-1.0.0.zip
```

### zoo export

```
llm-cli zoo export target|recipe|knowledge [SOURCE] --author NAME [Optionen]
```

Macht ein Paket aus deiner eigenen Arbeit: ein Target (`SOURCE`: der Ordnername des Targets), ein Rezept (`SOURCE`:
der Ordner eines fertigen Builds oder seine `build_info.json`) oder deine Wissensbasis (ohne `SOURCE`). Das Paket
wird datenschutzbereinigt, so geprüft, wie eine Installation es prüfen würde, mit deinem Schlüssel signiert und in
einen Ordner geschrieben; nichts wird gesendet. Die Zusammenfassung auf stdout zeigt die Datei, den Schlüssel, mit
dem signiert wurde, einen Diff für jede Datei, die die Datenschutz-Bereinigung geändert hat (höchstens 40 Zeilen je
Datei), und Hinweise.

| Argument / Option | Bedeutung |
|---|---|
| `target\|recipe\|knowledge` | Art des Pakets. |
| `SOURCE` | Der Ordnername des Targets oder ein Build-Ordner bzw. seine `build_info.json`. Pflicht für `target` und `recipe` (ohne: Exit-Code 2). |
| `--author TEXT` | **Pflicht.** Name, der als Autor angezeigt wird; ein Pseudonym ist in Ordnung. |
| `--license TEXT` | Lizenz des Pakets als SPDX-ID. Standard `MIT`; für Wissen `NOASSERTION`, weil die Seiten unter den Bedingungen ihrer Eigentümer bleiben. |
| `--model-license TEXT` | Rezepte: Lizenz-ID des Modells (Standard `unknown`). |
| `--id TEXT` | Wissen: ID des Pakets (Standard `knowledge-` und das heutige Datum, z. B. `knowledge-20261011`). |
| `--to FILE` | Das Paket zum privaten Teilen an die age-Empfänger (`age1...`) in dieser Datei verschlüsseln: was `llm-cli zoo keys` auf dem Rechner des Empfängers ausgegeben hat, oder ein öffentlicher Schlüssel aus `age-keygen`/`rage-keygen`; wiederholbar. |
| `--passphrase` | Das Paket zum privaten Teilen mit einer Passphrase verschlüsseln (zweimal abgefragt, mindestens 12 Zeichen). Nicht zusammen mit `--to` (Exit-Code 2): age verschlüsselt an Schlüssel oder an eine Passphrase. |
| `--out DIRECTORY` | Ordner für das Paket. Standard: `community/contributions/` in der Installation. |
| `--pr` | Das Paket an einen Pull-Request im Community-Repository übergeben (siehe unten). |
| `--yes` | Mit `--pr`: ohne Nachfrage nach der Zusammenfassung einreichen. |

Was in das Paket kommt:

- **target:** jede Datei des Target-Ordners. Links, Spezialdateien und Binärdateien werden abgelehnt, und die
  Target-Prüfungen müssen bestehen, wozu ein gültiger Block `capabilities:` gehört. Die Version ist
  `metadata.version` aus der `target.yml`, wenn sie die Form `1.2.3` hat (ein Zusatz wie `-rc1` ist erlaubt), sonst
  `1.0.0`. Ein Target, das du aus dem Zoo installiert hast, wird abgelehnt: Es bleibt das Paket seines Autors.
- **recipe:** nur ein fertiger Build eines Hugging-Face-Modells, dessen `build_info.json` den Commit des Modells und
  den Fingerabdruck des Targets enthält (Builds dieser Version). Ist das Target dein eigenes, erinnert die
  Zusammenfassung daran, es ebenfalls zu exportieren, sonst können andere das Rezept nicht bauen.
- **knowledge:** deine Seiten der Wissensbasis (sie muss eingeschaltet sein), datenschutzbereinigt. Jede Seite
  behält ihre Quelladresse; Seiten ohne Quelle bleiben draußen, ebenso Seiten aus installierten
  Community-Wissenspaketen (ein Hinweis sagt, wie viele).

Die Datei heißt `contribution-<Art>-<ID>-<Version>.zip`, verschlüsselt `<Art>-<ID>-<Version>.lcfpack`. Deine
Schlüssel werden beim ersten Mal im Secrets-Speicher angelegt (siehe [zoo keys](#zoo-keys)). Der Empfänger eines
verschlüsselten Pakets öffnet es mit `llm-cli zoo install --file`. Ein Paket mit Passphrase oder eines, das für
einen Empfänger aus `age-keygen` oder `rage-keygen` verschlüsselt ist, öffnet sich auch ohne das Framework:
entpacken, dann `age -d -i key.txt -o pack.zip pack.zip.age` (`key.txt` ist diese Identitätsdatei; bei einer
Passphrase entfällt `-i key.txt`; das Werkzeug `age` oder `rage` prüft die Signatur des Absenders nicht). Die
age-Identität von `llm-cli zoo keys` bleibt im Secrets-Speicher; Pakete, die für sie verschlüsselt sind, brauchen
also `llm-cli zoo install`.

Die Datenschutz-Bereinigung ersetzt Home-Ordner durch `~` sowie IP- und MAC-Adressen, E-Mail-Adressen, JWTs, den
Hostnamen dieses Rechners und deinen Benutzernamen durch Platzhalter wie `<ip>` und `<user>`, und sie schwärzt
API-Schlüssel. Loopback-Adressen, Image-Digests (`name@sha256:...`) und Standardkonten wie `/root` bleiben. Lies den
Diff: Ein Skript, dessen Adresse ersetzt wurde, funktioniert womöglich nicht mehr.

**Pull-Request (`--pr`).** Nach der Zusammenfassung fragt der Befehl „Submit this pack to the public community
repository?“. Antwortest du mit Nein oder gibt es kein Terminal und kein `--yes`, wird nichts eingereicht
(Exit-Code 1). Danach:

- Sind die GitHub CLI und git installiert und ist `gh` angemeldet (`gh auth login`), forkt der Befehl das Repository
  aus der Einstellung `community_repo` (`Besitzer/Name`), committet das Paket auf einem neuen Branch deines Forks nach
  `contributions/<Art>/` und öffnet den Pull-Request mit `gh pr create`.
- Sonst, oder wenn das scheitert, öffnet er die Upload-Seite des Repositorys
  (`https://github.com/<Besitzer>/<Name>/upload/main/contributions/<Art>`) in deinem Browser. Zieh die Datei dorthin;
  GitHub legt den Fork für dich an.

Das Framework fragt nie nach einem Token, liest keines und zeigt keines an: `gh` und `git` nutzen ihre eigene
gespeicherte Anmeldung. Verschlüsselte Pakete sind zum privaten Teilen gedacht und werden nie eingereicht
(Exit-Code 1). Solange `community_repo` leer ist, wie derzeit, bricht der Befehl mit „No community repository is
configured“ und Exit-Code 1 ab; das Paket bleibt in seinem Ordner. Die CI des Community-Repositorys prüft jedes
eingereichte Paket mit `llm-cli zoo verify` ([CONTRIBUTING](../CONTRIBUTING_DE.md#beiträge-zum-community-zoo)).

```bash
# Ein Target, geprüft so, wie die CI des Community-Repositorys es prüfen wird
llm-cli zoo export target my-board --author "<dein Name>"
llm-cli zoo verify community/contributions/contribution-target-my-board-1.0.0.zip

# Ein fertiger Build als Rezept
llm-cli zoo export recipe output/build_20261011_141500/Rockchip/SmolLM2-135M-Instruct/Q4_0 \
  --author "<dein Name>" --model-license Apache-2.0

# Deine Wissensbasis unter einer ID deiner Wahl
llm-cli zoo export knowledge --author "<dein Name>" --id rk3588-notes

# Verschlüsselt für eine Kollegin, oder mit einer Passphrase
llm-cli zoo export target my-board --author "<dein Name>" --to kollegin.keys
llm-cli zoo export target my-board --author "<dein Name>" --passphrase

# Exportieren und einen Pull-Request öffnen
llm-cli zoo export target my-board --author "<dein Name>" --pr
```

### zoo keys

```
llm-cli zoo keys
```

Gibt deine öffentlichen Schlüssel aus: einen age-Empfänger (`age1...`), an den andere Pakete für dich mit
`llm-cli zoo export --to` verschlüsseln (sein privater Teil bleibt im Secrets-Speicher, eine mit den age-Werkzeugen
dafür verschlüsselte Datei lässt sich also nicht öffnen), und eine Zeile `ed25519:`, mit der deine Signaturen geprüft
werden (andere nutzen sie mit `--from`). Sie sind nicht geheim; speichere die Ausgabe in einer Datei und gib sie den
Leuten, mit denen du Pakete austauschst. Die Schlüssel werden beim ersten Mal angelegt und nur im verschlüsselten
Secrets-Speicher aufbewahrt (Namen `community_signing_key` und `community_age_identity`). Ein gespeicherter
Schlüssel, der sich nicht lesen lässt, wird nie automatisch ersetzt; bei einem beschädigten nennt die Fehlermeldung
den Befehl, der einen neuen Schlüssel speichert.

```bash
llm-cli zoo keys > my.keys
```

```
# LCF public keys, key id 0123456789abcdef. Not secret: give them to people who send you
# encrypted packs (llm-cli zoo export --to) or check your signed packs.
age1<58 Zeichen>
ed25519:<base64>
```

---

## Bekannte Einschränkungen

- `build start --optimization` wirkt sich auf Rockchip-Builds nicht aus (die Skripte haben keine passende
  Einstellung); `--parallel` ist veraltet und hat keine Wirkung.
- Überschreibungen aus `config repos` erreichen die Builds als `<NAME>_REPO_OVERRIDE`, aber die Rockchip-Skripte
  lesen sie nicht.
- `deploy package --docker` ist experimentell.
- Einen CLI-Befehl, um Dittos Anbieter dauerhaft zu wählen, gibt es noch nicht; nutze die GUI, die `config.yml` oder
  `LLM_FRAMEWORK_AI_PROVIDER` (`ditto login` meldet an, wechselt den Anbieter aber nicht). Anmeldungen im Browser
  und bei Cloud-Plattformen (OAuth, AWS, Google Cloud, Azure) kommen später ([Roadmap](BLUEPRINT_DE.md#roadmap)).
- Das Community-Repository und sein signierter Index sind noch nicht veröffentlicht (`community_index_url` und
  `community_repo` sind leer, `configs/community_keys.yml` enthält keinen Schlüssel): `zoo list` zeigt nur
  installierte Pakete, und `zoo install <ID>` und `zoo export --pr` brechen mit einer Meldung ab. Pakete lassen sich
  als Dateien teilen.
- `build start` hat keine Option für einen Modell-Commit: Der festgelegte Commit eines Rezepts wird genutzt, wenn der
  Builder aus dem Community Hub ausgefüllt wird oder wenn ein KI-Client dem MCP-Tool `start_build` eine `revision`
  übergibt.
- Für Deep Ingest (Dokumentationsseiten zur Wissensbasis hinzufügen) gibt es keinen CLI-Befehl; es läuft im
  Modul-Assistenten der GUI (**Tools > Create Hardware Module...**).
