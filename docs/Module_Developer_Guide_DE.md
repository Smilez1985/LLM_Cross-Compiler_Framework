Version 3.0.0 + unveröffentlichte Änderungen · Stand 2026-10-11

[English](Module_Developer_Guide.md) · **Deutsch**

# Leitfaden für Modulentwickler

Dieser Leitfaden erklärt, wie du dem LLM Cross-Compiler Framework Unterstützung für neue Hardware hinzufügst. Ein
**Target** ist eine Hardware-Familie mit eigenem Werkzeug-Image (etwa Rockchip); ein **Board** ist ein Chip oder
Gerät darin (etwa RK3566). Jedes Target ist ein Ordner unterhalb von `targets/`. Das Rockchip-Target ist das
Vorbild: Wenn dieser Leitfaden „siehe Rockchip“ sagt, schau in `targets/Rockchip/`.

Du brauchst die [Entwicklungsumgebung](../CONTRIBUTING_DE.md#1-entwicklungsumgebung), Docker und ein Gerät der neuen
Hardware (oder ihre technischen Daten).

## Inhalt

1. [Wie das Framework ein Target nutzt](#1-wie-das-framework-ein-target-nutzt)
2. [Ordneraufbau](#2-ordneraufbau)
3. [Ein Target anlegen](#3-ein-target-anlegen)
4. [target.yml und der Capabilities-Block](#4-targetyml-und-der-capabilities-block)
5. [Das Dockerfile](#5-das-dockerfile)
6. [Das Build-Skript](#6-das-build-skript)
7. [Regeln der Sandbox](#7-regeln-der-sandbox)
8. [Ein Target testen](#8-ein-target-testen)
9. [Ein Target teilen](#9-ein-target-teilen)
10. [Altlasten](#10-altlasten)

## 1. Wie das Framework ein Target nutzt

1. **Laden.** Beim Start liest der `TargetManager` die `target.yml` in jedem Ordner unterhalb von `targets/`.
   Ordner, deren Name mit `_` oder `.` beginnt (etwa `_template`), werden übersprungen, ebenso eine `target.yml`
   ohne Block `metadata:`. Die Target-ID ist `metadata.id`, wenn gesetzt, sonst der Ordnername. Die Capabilities
   werden über die ID gesucht, die Build-Engine findet das Target aber über seinen Ordnernamen
   (`llm-cli build start -t <Ordner>`). Lass `metadata.id` also weg (oder setze es gleich dem Ordnernamen); sonst
   werden entweder die Capabilities nicht gefunden oder der Build findet den Target-Ordner nicht.
2. **Capabilities.** Der Block `capabilities:` wird gegen das Schema in `orchestrator/schemas/targets.py` geprüft.
   Die GUI bietet genau die dort aufgeführten Formate, Boards und Quantisierungen an, die CLI lehnt alles andere ab,
   und die Tools des MCP-Servers und Dittos starten Builds nur für Targets mit gültigem Block. Ein ungültiger Block
   wird als Fehler protokolliert, und das Target wird dann behandelt, als hätte es keinen.
3. **Image-Build.** Für jeden Build stellt die `BuildEngine` einen Build-Kontext zusammen (`modules/` und
   `scripts/` des Targets, das gemeinsame `entrypoint.sh` als `docker/entrypoint.sh` und das Dockerfile) und baut
   das Image `llm-framework/<Target>:<Build-ID>`. Dieser Schritt hat Netzwerk.
4. **Stufen.** Zuerst wird das Modell auf dem Host bereitgestellt. Dann läuft `modules/build.sh` in abgeschotteten
   Containern ohne Netzwerk: einmal mit `JOB_TYPE=imatrix`, wenn für GGUF eine Importance-Matrix gewünscht ist,
   dann mit `JOB_TYPE=build`.
5. **Ergebnisse.** Dateien, die `build.sh` in den Ausgabeordner schreibt, werden nach `output/` kopiert. Die Engine
   ergänzt `build_info.json` um Größe und SHA256-Hash jeder Modelldatei und schreibt `Model_Card.md` und ein ZIP.
   Das Deployment findet Builds über dieses Manifest.

## 2. Ordneraufbau

```text
targets/Rockchip/
├── target.yml           Metadaten und Capabilities
├── dockerfile           Build-Umgebung („Dockerfile“ geht ebenso)
├── modules/
│   ├── build.sh         Verteiler: der einzige Einstieg jeder Stufe
│   ├── rkllm_module.sh  RKLLM-Export (RK3588, RK3576)
│   ├── rknn_module.sh   RKNN-Umwandlung
│   └── source_module.sh, config_module.sh, convert_module.sh, target_module.sh   (Altlasten, nicht aufgerufen)
└── scripts/
    ├── export_rkllm.py
    └── rknn_converter.py
```

| Datei | Nötig | Wofür |
|---|---|---|
| `target.yml` mit Block `metadata:` | ja | Ohne ihn ist der Ordner kein Target. |
| Block `capabilities:` in `target.yml` | dringend empfohlen; Pflicht, um das Target zu teilen | Ohne ihn können GUI und CLI keine Einstellungen prüfen, die MCP-Tools und Ditto keine Builds für das Target starten, `llm-cli doctor` warnt, und das Target lässt sich nicht als Community-Paket exportieren oder installieren. |
| `Dockerfile` oder `dockerfile` | ja | Das Image, in dem alle Stufen laufen. |
| `Dockerfile.gpu` | optional | Für Builds mit durchgereichter GPU (`llm-cli build start --gpu` oder „Use Host GPU for Build Process“ in der GUI, unter Linux standardmäßig angehakt); ohne diese Datei wird das normale Dockerfile genutzt. |
| `modules/build.sh` | ja | Läuft in jeder Stufe als `/app/modules/build.sh`. |
| `scripts/` | optional | Hilfsskripte; werden neben `modules/` ins Image kopiert. |

## 3. Ein Target anlegen

### 3.1 Das Board untersuchen

Kopiere `scripts/hardware_probe.sh` auf das Gerät und führe es dort aus (auf Windows-Geräten:
`scripts/hardware_probe.ps1`):

```bash
bash hardware_probe.sh
```

Es schreibt `target_hardware_config.txt` in den aktuellen Ordner: CPU-Modell, Kerne und Flags, Speicher, Hersteller-
IDs von GPU und NPU und mehr. Die Datei enthält auch den Hostnamen des Geräts; entferne ihn, bevor du die Datei
teilst. Fehlt ein Hilfswerkzeug, bietet das Skript an, es zu installieren; mit `NON_INTERACTIVE=true` wird das
übersprungen.

Im Build-Reiter der GUI führt **Scan / Import... > Remote Scan via SSH...** die Probe per SSH auf einem Gerät aus (mit
derselben Prüfung beim ersten Kontakt wie das Deployment) und holt das Ergebnis ab. `llm-cli scan` führt die Probe auf
dem Rechner aus, an dem du arbeitest; das hilft nur, wenn dieser Rechner die Ziel-Hardware ist.

### 3.2 Das Gerüst erzeugen

Es gibt drei Wege; alle enden im selben Modulgenerator (`orchestrator/Core/module_generator.py`).

- **GUI-Assistent:** **Tools > Create Hardware Module...** Lies die Probe-Datei mit dem Standard-Import (feste
  Auswertung) ein oder lass Ditto sie analysieren, füge auf Wunsch mit „Deep Ingest“ Dokumentationsseiten zur
  Wissensbasis hinzu, prüfe dann die Seiten für Hardware, Docker und Flags und bestätige die Zusammenfassung. Für
  `aarch64`, `armv7l` und `riscv64` ergänzt der Assistent die passenden Cross-Compiler-Pakete. Die letzte Seite
  („Zusammenfassung“) hat zwei Kästchen, beide standardmäßig aus: den Wissens-Schnappschuss
  ([Abschnitt 3.4](#34-was-der-generator-schreibt-und-was-du-ergänzt)) und „Export for community after creating“
  ([Abschnitt 9.2](#92-ein-target-für-die-community-exportieren)).
- **CLI, interaktiv:** `llm-cli module create` fragt nach Name, Architektur (`aarch64`, `x86_64`, `armv7l`,
  `riscv64`), SDK, Basis-Image, Beschreibung, Paketen und CPU-Flags. Führe den Befehl im Installationsordner aus:
  Anders als die übrigen Befehle löst er ein relatives `targets_dir` gegen den aktuellen Ordner auf.
- **CLI mit Ditto:** `llm-cli module generate-ai target_hardware_config.txt` (auf Wunsch mit `--provider` und
  `--model` für diesen Lauf). Ditto liefert einen strukturierten Vorschlag (Architektur, SDK, Basis-Image, Pakete,
  CPU- und CMake-Flags, Einrichtungsbefehle, Quantisierungsblock). Ist die Wissensbasis eingeschaltet, zieht es
  vorher passende Dokumentationsauszüge hinzu. Der Vorschlag wird vollständig angezeigt; geschrieben wird erst nach
  deiner Bestätigung. Leerzeichen im Modulnamen werden zu Unterstrichen.

### 3.3 Was der Generator prüft

Jeder Wert landet in einem Dockerfile, in YAML oder in einem Shell-Skript; deshalb wird er geprüft, bevor eine
Vorlage ausgefüllt wird:

- **Name:** 1 bis 64 Buchstaben, Ziffern, Leerzeichen, `_`, `.` oder `-`, beginnend mit Buchstabe oder Ziffer. Der
  Ordnername ist der kleingeschriebene Name mit Unterstrichen (aus `My Board` wird `targets/my_board/`).
- **Architektur:** eine von `aarch64`, `x86_64`, `armv7l`, `riscv64`.
- **Basis-Image:** muss per Digest festgelegt sein (`image@sha256:...`), außer es beginnt mit `debian:`, `ubuntu:`,
  `nvidia/cuda:`, `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` oder `python:`.
- **Pakete:** gültige Debian-Paketnamen. **Flags:** einfache Compiler- oder CMake-Optionen wie `-mcpu=cortex-a76`
  oder `-DGGML_CUDA=ON`.
- **Shell-Code** (Einrichtungsbefehle, Quantisierungsblock): keine Downloads (`curl`, `wget`, ...), kein `sudo`,
  kein Löschen von `/`, kein `chmod 777`, kein Schreiben auf Rohgeräte, kein Docker-Socket und kein `docker run`,
  kein Ausführen erzeugten Codes; keine Steuerzeichen. Jedes erzeugte Skript muss `bash -n` bestehen.
- **Ort:** Das Target entsteht nur direkt unterhalb von `targets/`, nie über einem bestehenden Target (auch nicht
  bei anderer Groß- und Kleinschreibung), und es wird wieder entfernt, wenn die Erzeugung scheitert.
- **Der fertige Ordner:** Bevor der Generator Erfolg meldet, muss das neue Target dieselben Prüfungen bestehen wie
  ein Community-Paket ([Abschnitt 9.1](#91-was-die-prüfungen-verlangen)), darunter, dass nirgends ein Platzhalter der
  Vorlage übrig ist. Scheitert etwas, wird der Ordner entfernt, und der Fehler führt die Probleme auf.

### 3.4 Was der Generator schreibt und was du ergänzt

Der Generator schreibt `target.yml` (Metadaten, `supported_boards`, `docker`, `modules`, `optimization_profiles`,
`requirements` und einen Block `capabilities:`), ein `Dockerfile` (aus dem `Dockerfile.gpu` der Vorlage, wenn das
SDK „cuda“ oder das Basis-Image „nvidia“ enthält), `modules/build.sh`, `modules/config_module.sh`,
`modules/source_module.sh`, `modules/rkllm_module.sh`, `modules/rknn_module.sh`, `modules/benchmark_module.sh`,
Platzhalter für `convert_module.sh` und `target_module.sh`, `generate_profile.sh` und die `scripts/` der Vorlage.

**Der Capabilities-Block.** Jedes erzeugte Target bekommt einen, mit der Unterstützungsstufe `community`; so prüfen
der Builder, die CLI und KI-Clients seine Build-Einstellungen von Anfang an. Für ein Modul namens „My Board“ sieht er
so aus:

```yaml
capabilities:
  schema_version: 1
  support_level: community
  default_board: my_board
  boards:
    my_board:
      label: My Board
  formats:
    gguf:
      label: GGUF (llama.cpp)
      input: [hf]
      quantizations: [Q4_K_M, Q4_0, Q5_K_M, Q6_K, Q8_0, F16]
      default_quantization: Q4_K_M
      imatrix: true
      dataset: optional
```

- **Boards:** die Boards, die der Generator bekommt; die ID ist der kleingeschriebene Name mit Unterstrichen, das
  Label der Name. Der GUI-Assistent und `llm-cli module create` übergeben keine, `generate-ai` übergibt den
  Modulnamen; in der Praxis ist also der Modulname das einzige Board.
- **Quantisierungen:** die Bezeichnungen der `case`-Zweige in der Quantisierungslogik des Assistenten oder des
  Vorschlags von Ditto (zum Beispiel `Q4_K_M)` oder `Q4_K_M|Q5_K_M)`; nur Namen aus Buchstaben, Ziffern und `_`).
  Ohne solche Zweige: `Q4_K_M`, `Q4_0`, `Q5_K_M`, `Q6_K`, `Q8_0` und `F16`. Die erste ist der Standard. Die
  Bezeichnungen werden übernommen, wie sie dastehen, `Q4_K_M|q4_k_m)` führt also beide Schreibweisen auf. Behalte
  eine Schreibweise im Block: Anfragen werden ohne Beachtung der Groß- und Kleinschreibung zugeordnet, und andere
  Namen gehören in `aliases` (zum Beispiel `INT8: Q8_0`).

**Wissens-Schnappschuss (nur auf Wunsch).** Auf der letzten Seite des Assistenten ist das Kästchen „Attach the
knowledge crawled for this target (Deep Ingest, privacy scrub applied)“ standardmäßig aus und lässt sich erst
einschalten, wenn in diesem Assistenten Deep Ingest gelaufen ist. Dann exportiert der Generator nur die Seiten, die
von den in diesem Assistenten eingegebenen URLs gecrawlt wurden, datenschutzbereinigt (Home-Ordner, Adressen, Host-
und Benutzernamen, Schlüssel), nach `knowledge/initial_knowledge.json`, mit einer `README.md` daneben, die sagt,
was die Datei enthält. Solange ein Deep Ingest noch läuft, bleibt Finish gesperrt, damit der Schnappschuss jede
gecrawlte Seite enthält; stammt keine gespeicherte Seite von diesen URLs, sagt der Assistent, dass kein
Schnappschuss angehängt wurde. Sonst kommt nichts aus deiner Wissensbasis in das Modul, und ohne das Kästchen gibt es
keinen Ordner `knowledge/`. `llm-cli module create` und `generate-ai` hängen nie Wissen an. Das Framework nimmt
diese Datei nicht in eine Wissensbasis auf, und die Platzhalterprüfung des Generators lässt sie aus (gecrawlte
Seiten können durchaus Text wie `YYYY-MM-DD` enthalten). Die Seiten bleiben unter den Bedingungen ihrer Eigentümer;
die Lizenz eines Pakets deckt sie nicht ab.

Das Ergebnis ist ein **Gerüst**, noch kein funktionierendes Target:

- **Der Capabilities-Block ist ein Ausgangspunkt.** Ergänze deine echten Boards, weitere Formate, Quantisierungen je
  Board und den Bedarf an Kalibrierdaten ([Abschnitt 4](#4-targetyml-und-der-capabilities-block)), und halte den
  Block im Einklang mit `build.sh`.
- **Das Vorlagen-`build.sh` passt nicht zur aktuellen Pipeline.** Es sucht `convert-hf-to-gguf.py` und die
  llama.cpp-Werkzeuge an Stellen, an denen aktuelle llama.cpp-Builds sie nicht ablegen, schreibt die F16-Zwischendatei
  nach `/tmp` (in der Sandbox ein 1-GB-tmpfs) und kopiert im Zweifel das Modell unverändert. Das Vorlagen-Dockerfile
  baut llama.cpp nicht. Für GGUF nimm `targets/Rockchip/dockerfile` und `targets/Rockchip/modules/build.sh` als
  Ausgangspunkt oder das Beispiel in [Abschnitt 6](#6-das-build-skript). Der erzeugte Block gibt trotzdem GGUF an;
  ein Build des unveränderten Gerüsts wird also angenommen und scheitert dann oder kopiert das Modell.

Eine überarbeitete Vorlage ist für 3.4.0 geplant (siehe [Roadmap](BLUEPRINT_DE.md#roadmap)).

## 4. target.yml und der Capabilities-Block

### 4.1 metadata

```yaml
metadata:
  name: "My Board"
  description: "CPU-only GGUF builds for My Board"
  maintainer: "Dein Name"
  version: "1.0.0"
  architecture_family: "aarch64"
  vendor: "community"
  # id: weglassen; die Target-ID muss dem Ordnernamen entsprechen (siehe Abschnitt 1)
```

### 4.2 capabilities

Der Block ist die einzige Quelle dafür, was das Target bauen kann. GUI, CLI, Build-Service, Ditto und der
MCP-Server leiten ihre Auswahl und ihre Prüfungen daraus ab.

```yaml
capabilities:
  schema_version: 1
  support_level: community
  default_board: my_board        # das Board, das build.sh ohne Angabe nutzt
  boards:
    my_board:
      label: "My Board (4x Cortex-A76)"
      cpu: "4x Cortex-A76"
      ram_max_gb: 8
      examples: ["My Board 8 GB"]
  formats:
    gguf:
      label: "GGUF (llama.cpp, CPU)"
      input: [hf]
      quantizations: [Q4_K_M, Q4_0, Q5_K_M, Q8_0, IQ2_XS]
      default_quantization: Q4_K_M
      imatrix: true
      needs_imatrix: [IQ2_XS]
      dataset: optional
      dataset_kind: text
      aliases: {INT8: Q8_0, Q4: Q4_K_M}
      notes: "Runs on the CPU."
```

Oberste Ebene:

| Feld | Bedeutung |
|---|---|
| `schema_version` | Immer `1`. |
| `support_level` | `official` (vom Projekt gepflegt und getestet), `community` (beigesteuert; funktioniert bei seinen Pflegern) oder `experimental` (unvollständig oder ungeprüft). Vorgabe: `official`. |
| `default_board` | Das Board, das `build.sh` nutzt, wenn ein Auftrag keins nennt. Muss in `boards` stehen und zur Vorgabe in deinem `build.sh` passen. |
| `boards` | Mindestens ein Board: ID → `label` (Pflicht), `cpu`, `ram_max_gb` (größer als 0), `npu`, `gpu`, `examples`. |
| `formats` | Mindestens ein Ausgabeformat: ID → die Felder unten. |

Je Format:

| Feld | Bedeutung |
|---|---|
| `label` | Für Benutzer angezeigter Name (Pflicht). |
| `input` | Angenommene Modelleingaben: beliebige aus `hf` (Hugging-Face-Modell), `onnx`, `gguf`. Vorgabe: `[hf]`. Dittos Build-Empfehlungen nutzen das. |
| `quantizations` | Alle Typen, die dieses Format annimmt (mindestens einer). |
| `default_quantization` | Gilt, wenn ein Auftrag keinen nennt; muss in `quantizations` stehen. |
| `boards` | Boards, auf denen dieses Format läuft; fehlt das Feld, gelten alle Boards des Targets. |
| `board_quantizations` | Teilmengen von `quantizations` je Board, wo ein Chip weniger Typen kann. |
| `board_default_quantization` | Vorgabe je Board; muss auf diesem Board erlaubt sein. |
| `imatrix` | `true`, wenn das Format eine Importance-Matrix nutzen kann (GGUF). |
| `needs_imatrix` | Typen, die ohne Importance-Matrix abgelehnt werden. Verlangt `imatrix: true`. |
| `dataset` | Kalibrierdaten: `none`, `optional` oder `required`. |
| `dataset_kind` | `text` (eine Textdatei) oder `sample_list` (eine Liste von Beispieleingaben, wie RKNN sie nutzt). |
| `dataset_required_for` | Typen, die einen Datensatz brauchen, auch wenn `dataset` auf `optional` steht. |
| `aliases` | Andere Schreibweisen, die deine Skripte annehmen, etwa `{INT8: Q8_0}`; ohne Beachtung der Groß- und Kleinschreibung verglichen. Jeder Alias muss auf einen aufgeführten Typ zeigen. |
| `notes` | Kurzer Hinweis für Benutzer und für Ditto. |

Das Schema lehnt widersprüchliche Blöcke mit einer Meldung ab, etwa eine Vorgabe, die nicht in der Liste steht, eine
Board-Vorgabe, die auf diesem Board nicht erlaubt ist, unbekannte Boards in `boards`, `board_quantizations` oder
`board_default_quantization`, `needs_imatrix` ohne `imatrix: true` oder einen Alias, der auf einen unbekannten Typ
zeigt. Der Rockchip-Block zeigt jedes Feld im Einsatz, auch `board_quantizations` für RKLLM und `sample_list` für
RKNN.

### 4.3 Wie ein Auftrag vereinheitlicht und geprüft wird

Bevor ein Build startet, werden die Werte vereinheitlicht und dann geprüft; der Build läuft genau mit den
vereinheitlichten Werten:

1. Format, Board und Quantisierung werden ohne Beachtung der Groß- und Kleinschreibung verglichen; eine
   Quantisierung darf auch einer der Aliasse sein.
2. Ein fehlendes Board wird zum einzigen Board des Formats, sonst zu `default_board`.
3. Eine fehlende Quantisierung wird zur Vorgabe des Boards, dann zur Vorgabe des Formats (oder zum ersten auf dem
   Board erlaubten Typ).
4. Probleme kommen als Sätze zurück, die die erlaubten Werte nennen.

Mit dem Rockchip-Block:

| Auftrag | Ergebnis |
|---|---|
| `-f gguf -b RK3566` | Board `rk3566`, Quantisierung `Q4_0` (Vorgabe des Boards) |
| `-f rkllm -q int4` | Board `rk3588` (Standard-Board), Quantisierung `w4a16` (Alias), dann abgelehnt: „Quantization 'w4a16' is not supported for rkllm on rk3588 (allowed: w8a8, w8a8_g128, w8a8_g256, w8a8_g512).“ |
| `-f gguf -b rk3566 -q IQ2_XS` ohne IMatrix | abgelehnt: „IQ2_XS needs an importance matrix: enable IMatrix.“ |

### 4.4 Weitere Blöcke

Die Build-Pipeline liest nur `metadata` und `capabilities`. Ein optionaler Block `hardware:` (`cpu.architecture`,
`gpu.vendor`, `gpu.vendor_id`, `npu.vendor`, `npu.vendor_id`) wird an zwei Stellen genutzt: um beim Einlesen einer
Probe-Datei im Build-Reiter ein passendes Target vorzuschlagen, und für Geräteoptionen in den Startbefehlen eines
Deployment-Pakets mit Docker-Images. Die übrigen Blöcke in der Rockchip-`target.yml` (`supported_boards`, `docker`,
`modules`, `optimization_profiles`, `requirements` usw.) sind beschreibend.

## 5. Das Dockerfile

Der Build-Kontext, den die Engine zusammenstellt, sieht so aus; das Dockerfile kopiert also von diesen Pfaden:

```text
<Kontext>/
├── Dockerfile           dein Dockerfile (oder Dockerfile.gpu)
├── modules/             deine modules/
├── scripts/             deine scripts/ (leer, wenn du keine hast)
└── docker/entrypoint.sh das gemeinsame entrypoint.sh
```

Build-Argumente, die die Engine übergibt:

| Argument | Wert |
|---|---|
| `LLAMA_CPP_TAG` | die Einstellung `llama_cpp_version` (Vorgabe `v0.6.0`) |
| `BUILD_JOBS` | die Einstellung `build_jobs`, nur wenn sie größer als 0 ist; das Self-Healing senkt sie auf 1, wenn einem Compiler der Speicher ausging |
| `USER_ID`, `GROUP_ID` | die IDs des Host-Benutzers (Linux und macOS, wenn der Host-Benutzer nicht root ist) |

Was das Image bieten muss (siehe `targets/Rockchip/dockerfile`):

- `/app/modules/build.sh`, ausführbar (`COPY modules/ ./modules/` und `chmod +x`).
- Den gemeinsamen Entrypoint als `ENTRYPOINT ["/entrypoint.sh"]` und einen Benutzer `llmbuilder`; startet ein
  Container als root (alter Modus ohne Sandbox), bildet der Entrypoint ihn auf den Host-Benutzer ab und wechselt zu
  `llmbuilder`.
- `ENV BUILD_CACHE_DIR=/build-cache` und jedes Werkzeug, das die Umwandlung braucht. Alles muss beim Bau des Images
  installiert werden, denn die Stufen haben kein Netzwerk.
- **Festgelegte Versionen:** ein per Digest festgelegtes Basis-Image (offizielle Basis-Images wie
  `debian:bookworm-slim` dürfen einen Tag nutzen, siehe [Abschnitt 9.1](#91-was-die-prüfungen-verlangen)), Git-Tags
  oder Commits, exakte pip-Versionen und SHA256-Prüfungen für heruntergeladene Dateien (wie `RKLLM_WHEEL_SHA256` für
  das RKLLM-Wheel).
- **Ein venv je Werkzeugkette**, wenn sich ihre Abhängigkeiten widersprechen (Rockchip: `/opt/venv`,
  `/opt/venv-rkllm`, `/opt/venv-rknn`).
- Keine Binärdateien im Repository: Wheels und Bibliotheken im Dockerfile herunterladen und ihre Hashes prüfen.

## 6. Das Build-Skript

`modules/build.sh` ist der einzige Einstieg. Es läuft einmal je Stufe, als `/app/modules/build.sh`, in der Sandbox
aus [Abschnitt 7](#7-regeln-der-sandbox).

### 6.1 Eingaben

Umgebungsvariablen, die die Engine setzt:

| Variable | Bedeutung |
|---|---|
| `JOB_TYPE` | `imatrix` (Importance-Matrix-Stufe) oder `build` |
| `MODEL_SOURCE` | Pfad des Modells im Container (Ordner oder Datei) |
| `MODEL_NAME` | für Dateinamen taugliche Modellbezeichnung, für die Namen der Ausgabedateien |
| `TARGET_FORMAT` | Format-ID aus den Capabilities, etwa `gguf` |
| `TARGET_BOARD` | Board-ID oder leer (dann dieselbe Vorgabe wie `default_board` nutzen) |
| `QUANTIZATION` | Quantisierungstyp, bereits vereinheitlicht; leer nur bei Targets ohne Capabilities |
| `TARGET_ARCH` | die Target-ID |
| `MODEL_TASK`, `OPTIMIZATION_LEVEL`, `BUILD_ID` | Aufgabe (Vorgabe `LLM`), Optimierungsstufe, Build-ID |
| `MAX_CONTEXT_LENGTH` | nur, wenn für den Build gesetzt |
| `USE_IMATRIX`, `IMATRIX_PATH` | Build-Stufe: `1` und der Pfad von `imatrix.dat`, wenn eine Importance-Matrix berechnet wurde |
| `DATASET_PATH` | Kalibrierdatensatz, wenn der Build einen nutzt |
| `BUILD_SCRATCH_DIR`, `TMPDIR`, `HOME`, `XDG_CACHE_HOME` | beschreibbarer Arbeitsordner `/build-cache/work` und seine Unterordner |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE` | `1` in der Sandbox: Bibliotheken versuchen nie das Netzwerk |
| `PUID`, `PGID` | die IDs des Host-Benutzers (nicht unter Windows) |

Einbindungen:

| Pfad im Container | Modus | Inhalt |
|---|---|---|
| `/build-cache/output` | lesen und schreiben (Build-Stufe) | wohin `build.sh` seine Ergebnisse schreibt |
| `/build-cache/imatrix` | lesen und schreiben in der Imatrix-Stufe, nur lesen in der Build-Stufe | `imatrix.dat` |
| `/build-cache/work` | lesen und schreiben | Zwischenablage für große Zwischendateien |
| `/build-cache/input-model` | nur lesen | ein lokaler Modellordner oder der Ordner einer lokalen Modelldatei |
| `/build-cache/models` | nur lesen in der Sandbox | Hugging-Face-Downloads unter `hf/<Besitzer>__<Name>` |
| `/build-cache/dataset.txt`, `/build-cache/dataset.json` oder `/build-cache/dataset/` | nur lesen | Kalibrierdatensatz (eine RKNN-Beispielliste bekommt ihren Ordner); ein Datensatz im Modellordner wird über die Modell-Einbindung erreicht |

### 6.2 Ausgaben und Exit-Code

- Schreib Modelldateien nach `$BUILD_CACHE_DIR/output` (`/build-cache/output`). Dateien mit der Endung `.gguf`,
  `.rkllm`, `.rknn`, `.onnx`, `.safetensors` oder `.bin` zählen als Modelldateien; die größte Datei mit der Endung
  des Formats wird zur Hauptdatei, die das Deployment installiert.
- Du darfst dort eine `build_info.json` mit eigenen Feldern schreiben (Rockchip schreibt Modell, Format, Board,
  Quantisierung und den llama.cpp-Tag); die Engine ergänzt Dateiliste, Hashes und Build-Daten.
- In der Imatrix-Stufe schreibst du `$BUILD_CACHE_DIR/imatrix/imatrix.dat`.
- Beende das Skript bei jedem Fehler mit einem Exit-Code ungleich 0 und schreib den Grund nach stderr. Die letzten
  Log-Zeilen gehen an den Benutzer und an das Self-Healing; formuliere sie also klar
  (`die "RKLLM needs an RK3588 or RK3576 NPU; ..."`).

### 6.3 Regeln

- Beginne mit `set -euo pipefail`.
- Schreib Format und Board klein, vereinheitliche die Quantisierung und nimm genau die Aliasse an, die du in den
  Capabilities angegeben hast. Lehne nicht unterstützte Werte mit einer Meldung ab, die das Unterstützte nennt.
- Nutze dasselbe Standard-Board wie `default_board`.
- Schreib große Zwischendateien nach `$BUILD_SCRATCH_DIR`, nie ins Image oder an einen festen Pfad unter `/tmp`.
- Lade nie etwas herunter; die Stufe hat kein Netzwerk.
- Halte Capabilities und Skript mit Tests im Einklang, so wie `tests/test_foundation.py` und
  `tests/test_review_capabilities_paths.py` es für Rockchip tun.

### 6.4 Beispiel

Ein minimaler GGUF-Verteiler für die Capabilities aus [Abschnitt 4.2](#42-capabilities). Er setzt voraus, dass das
Image llama.cpp in `/usr/src/llama.cpp` mit den Werkzeugen in `build/bin` enthält, wie das Rockchip-Image. Er besteht
`bash -n` und ShellCheck; für F16/BF16-Ausgabe, Aliasse und weitere Prüfungen siehe
`targets/Rockchip/modules/build.sh`.

```bash
#!/bin/bash
# build.sh - dispatcher of the target "my_board" (run by the BuildEngine inside the target image)
set -euo pipefail

WORK_DIR="${BUILD_CACHE_DIR:-/build-cache}"
OUTPUT_DIR="$WORK_DIR/output"
IMATRIX_DIR="$WORK_DIR/imatrix"
SCRATCH_DIR="${BUILD_SCRATCH_DIR:-$WORK_DIR}"   # large intermediates; the image filesystem is read-only
FORMAT="$(echo "${TARGET_FORMAT:-gguf}" | tr '[:upper:]' '[:lower:]')"
BOARD="$(echo "${TARGET_BOARD:-my_board}" | tr '[:upper:]' '[:lower:]')"   # same as default_board
QUANT="$(echo "${QUANTIZATION:-Q4_K_M}" | tr '[:lower:]' '[:upper:]')"
MODEL_NAME="${MODEL_NAME:-model}"
LLAMA_BASE="${LLAMA_CPP_PATH:-/usr/src/llama.cpp}"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -e "${MODEL_SOURCE:-}" ] || die "MODEL_SOURCE '${MODEL_SOURCE:-}' does not exist in the container."
case "$FORMAT" in
    gguf) ;;
    *) die "Format '$FORMAT' is not supported by this target (gguf)." ;;
esac
echo "Board: $BOARD, format: $FORMAT, quantization: $QUANT, job: ${JOB_TYPE:-build}"

f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
python3 "$LLAMA_BASE/convert_hf_to_gguf.py" "$MODEL_SOURCE" --outfile "$f16" --outtype f16

if [ "${JOB_TYPE:-build}" = "imatrix" ]; then
    [ -f "${DATASET_PATH:-}" ] || die "Calibration dataset not found at '${DATASET_PATH:-}'."
    mkdir -p "$IMATRIX_DIR"
    "$LLAMA_BASE/build/bin/llama-imatrix" -m "$f16" -f "$DATASET_PATH" -o "$IMATRIX_DIR/imatrix.dat" --chunks 100
    rm -f "$f16"
    exit 0
fi

imatrix_args=()
if [ "${USE_IMATRIX:-0}" = "1" ]; then
    [ -f "${IMATRIX_PATH:-}" ] || die "USE_IMATRIX=1 but '${IMATRIX_PATH:-}' is missing."
    imatrix_args=(--imatrix "$IMATRIX_PATH")
fi
mkdir -p "$OUTPUT_DIR"
"$LLAMA_BASE/build/bin/llama-quantize" "${imatrix_args[@]}" "$f16" "$OUTPUT_DIR/${MODEL_NAME}-${QUANT}.gguf" "$QUANT"
rm -f "$f16"
```

## 7. Regeln der Sandbox

Umwandlungsstufen führen Modellwerkzeuge und den Build-Code des Targets aus, der von einer KI erzeugt sein kann. Mit
der Vorgabe `build_sandbox: true` läuft jeder Stufen-Container:

- als Host-Benutzer, nicht als root (1000:1000, wenn der Host-Benutzer root ist oder unter Windows);
- **ohne Netzwerk** (`network_mode: none`); Modell und Datensatz wurden auf dem Host bereitgestellt;
- ohne Linux-Capabilities und mit `no-new-privileges`;
- mit **schreibgeschütztem Image-Dateisystem**; `/tmp` ist ein 1-GB-tmpfs;
- mit einem Prozesslimit (`build_pids_limit`, Vorgabe 4096) und, falls gesetzt, einem Speicherlimit
  (`build_memory_limit`, etwa `24g`);
- mit nur den eigenen Ordnern des Builds beschreibbar (Arbeitsordner, Ausgabe und in ihrer Stufe die
  Importance-Matrix); Modell-, Cache- und Datensatz-Einbindungen sind schreibgeschützt.

Mit `--gpu` werden NVIDIA-GPUs durchgereicht, wenn auf dem Host `nvidia-smi` vorhanden ist; sonst wird `/dev/dri`
durchgereicht, und der Container-Benutzer tritt den Gruppen bei, denen die Render-Geräte gehören.

Was das für dein Target heißt: Installiere alles im Dockerfile; schreib nicht ins Image; verlass dich nicht auf root,
`sudo` oder eine feste Benutzer-ID; leg große Dateien in `$BUILD_SCRATCH_DIR` ab; lies Modell und Datensatz nur über
die übergebenen Pfade. `build_sandbox: false` schaltet auf den alten Modus (root plus Entrypoint) zurück; nutze das
nur, um herauszufinden, ob ein Fehler an der Sandbox liegt.

## 8. Ein Target testen

1. **Ist das Target geladen und sind seine Capabilities gültig?** `llm-cli doctor` meldet den Code
   `targets.<ID>.capabilities`, wenn der Block fehlt oder ungültig ist; der Fehler selbst steht im Log. So siehst du
   den Schemafehler direkt:

   ```bash
   python -c "import yaml; from orchestrator.schemas.targets import TargetCapabilities as T; \
   T.model_validate(yaml.safe_load(open('targets/my_board/target.yml', encoding='utf-8'))['capabilities']); print('ok')"
   ```

   `llm-cli targets validate <ID>` bestätigt nur, dass `target.yml` geladen wurde; die Capabilities prüft es nicht.
2. **Baut das Image?** Stell den Kontext so zusammen wie die Engine und bau ihn:

   ```bash
   python3 -m orchestrator.utils.build_context targets/my_board /tmp/my_board-context
   docker build -t llm-framework/my_board:dev /tmp/my_board-context
   ```

3. **Ein echter Build mit einem kleinen Modell:**

   ```bash
   llm-cli build start -m HuggingFaceTB/SmolLM2-135M-Instruct -t my_board -f gguf -b my_board -F
   ```

   Prüfe den neuen Ordner in `output/`: die Modelldatei und `build_info.json` mit `artifacts` und `primary`.
4. **Lint:** ShellCheck läuft über jede versionierte `.sh`-Datei außerhalb von `targets/_template`
   ([CONTRIBUTING, Abschnitt 5](../CONTRIBUTING_DE.md#5-lint--und-sicherheitsprüfungen)). Nimm dein Dockerfile in die
   hadolint-Matrix in `.github/workflows/docker.yml` und in `.github/dependabot.yml` auf.
5. **Tests:** Ergänze Tests, die deine Capabilities und deine Skripte im Einklang halten (siehe die in
   [Abschnitt 6.3](#63-regeln) genannten Rockchip-Tests).
6. **Auf dem Gerät:** Deploye den Build (Deployment-Fenster oder `llm-cli deploy package` und `llm-cli deploy run`)
   und starte das Modell dort.
7. **Bereit zum Teilen?** Führe die Prüfungen aus [Abschnitt 9.1](#91-was-die-prüfungen-verlangen) für den Ordner
   aus; `ok` heißt: kein Problem.

   ```bash
   python -c "from pathlib import Path; from orchestrator.Core.module_generator import validate_target_folder as v; \
   print('\n'.join(v(Path('targets/my_board'))) or 'ok')"
   ```

## 9. Ein Target teilen

Es gibt zwei Wege:

- **Als Community-Paket**, für ein Target, das du selbst pflegst: Exportiere es als signiertes Paket und reiche es
  beim Community-Repository ein; Nutzer installieren es mit `llm-cli zoo install` oder dem Community Hub. Behalte
  `support_level: community` bei.
- **In diesem Repository**, wenn die Maintainer das Target übernehmen: Öffne einen Pull-Request mit dem Target-Ordner
  und beschreibe, welche Boards und Modelle du getestet hast. Folge den
  [Regeln für die Mitarbeit](../CONTRIBUTING_DE.md): keine Binärdateien, keine Secrets, festgelegte Versionen und
  Dokumentation auf Englisch und Deutsch.

In beiden Fällen muss das Target die Prüfungen unten bestehen. Target-Ordner von Hand nach `community/` zu kopieren
ist kein Weg mehr, sie zu installieren.

### 9.1 Was die Prüfungen verlangen

`validate_target_folder` in `orchestrator/Core/module_generator.py` prüft einen Target-Ordner, den andere bauen
werden. Die Prüfung läuft, wenn der Modulgenerator ein Target fertigstellt, wenn du ein Target exportierst
(`llm-cli zoo export target` oder der Export-Dialog), wenn ein Target-Paket installiert wird (im Quarantäne-Ordner)
und in `llm-cli zoo verify <Paketdatei>`, das die CI des Community-Repositorys ausführt. Jedes Problem wird mit
seiner Datei und bei Skripten mit seiner Zeile gemeldet.

| Prüfung | Regel |
|---|---|
| Capabilities | `target.yml` existiert und hat einen Block `capabilities:`, den das Schema annimmt ([Abschnitt 4.2](#42-capabilities)). |
| Festgelegte Images | In jeder Datei, deren Name in beliebiger Schreibweise mit `dockerfile` beginnt (`Dockerfile`, `dockerfile`, `Dockerfile.gpu`), muss jedes Image, das `FROM`, `COPY --from=` oder `RUN --mount=...,from=` verwendet, per Digest festgelegt sein (`name@sha256:` und 64 Hex-Ziffern). Ausgenommen sind offizielle Basis-Images mit Tag (Namen, die mit `debian:`, `ubuntu:`, `nvidia/cuda:`, `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` oder `python:` beginnen), eine frühere Build-Stufe (`FROM ... AS builder`, danach `COPY --from=builder`) und eine Stufennummer (`--from=0`). Kommentarzeilen werden übersprungen. Eine Variable wie `FROM ${BASE_IMAGE}` wird abgelehnt, weil sie keine Image-Angabe ist. |
| Shell-Sperrliste | Jede Zeile jeder `*.sh`-Datei, Kommentare eingeschlossen, außer die gleiche Zeile steht in der Datei der Vorlage mit demselben Pfad (für `modules/build.sh`: `targets/_template/modules/build.sh`). Abgelehnt werden: Downloads (`curl`, `wget`, `aria2c` und `fetch` als Wort, also auch `git fetch`), das Löschen von `/` (`rm -rf /`), `chmod 777`, `sudo`, `mkfs` und `dd if=`, der Docker-Socket oder `docker run`, `eval`, `source` oder `.` mit dem Ergebnis von `$(...)`, `sh -c "$(...)"` und Prozess-Substitution `<(...)`, außer als reine Eingabe (`done < <(grep ...)`). Downloads gehören festgelegt ins Dockerfile. |
| Shell-Syntax | Jede `*.sh`-Datei besteht `bash -n` (entfällt, wo bash nicht installiert ist). |
| Nur Text | Keine Datei enthält ein NUL-Byte: Binärdateien werden abgelehnt. Beim Export muss außerdem jede Datei UTF-8-Text sein. |
| Platzhalter | Keine Datei außer dem Wissens-Schnappschuss `knowledge/initial_knowledge.json` (gecrawlte Seiten) enthält einen Platzhalter der Vorlage (`[MODULE_NAME]`, `[Hardware-Familie]`, `[IHRE_ARCHITEKTUR]`, `[Hersteller]`, `[QUANTIZATION_LOGIC]`, `[CPU_FLAGS]`, `[CMAKE_FLAGS]`, `[SDK_SETUP_COMMANDS]`, `[PACKAGING_COMMANDS]`, `[ihr-target-name]`, `{packages_str}` oder `YYYY-MM-DD`), und kein Wert in `target.yml` enthält Text in eckigen Klammern wie `[Board Name 1]`. |

Das Paketformat bringt beim Export und bei der Installation eigene Regeln mit:

- Keine symbolischen Links, Junctions oder Spezialdateien irgendwo im Ordner.
- Pfade sind relativ und durch `/` getrennt, ohne `..`, Backslashes, Laufwerksbuchstaben, die Zeichen
  `: * ? " < > |`, Steuerzeichen oder einen Punkt oder ein Leerzeichen am Ende, und sie sind ohne Beachtung der
  Groß- und Kleinschreibung eindeutig.
- Höchstens 10.000 Dateien und 512 MB. Dateien behalten nur die Modi 0644 oder 0755 (ausführbar oder nicht).
- `pack.json`, `pack.sig` und `.lcf-provenance.json` sind oben im Ordner reservierte Namen.
- Die CI des Community-Repositorys nimmt Pakete bis 5 MB an, und `llm-cli zoo verify` lehnt ein Paket ab, in dem die
  Datenschutz-Bereinigung noch etwas ändern würde.

Das mitgelieferte Rockchip-Target besteht alle diese Prüfungen.

### 9.2 Ein Target für die Community exportieren

1. **Das Target fertigstellen:** Teste es mit einem echten Build ([Abschnitt 8](#8-ein-target-testen)), behalte
   `support_level: community` bei und setze `metadata.version` in der Form `1.2.3` (sonst bekommt das Paket
   `1.0.0`).
2. **Exportieren.**
   - In der GUI, für ein neues Target: Das Kästchen „Export for community after creating (a scrubbed, signed pack
     for a pull request)“ auf der letzten Seite des Assistenten öffnet nach dem Anlegen den Dialog „Export target
     for the community“. Gib einen Autorennamen ein (ein Pseudonym ist in Ordnung) und eine Lizenz (MIT, wenn du
     keine andere wählst), dann klicke auf „Create pack“. „Show folder“ öffnet den Ordner mit dem Paket.
   - Auf der Kommandozeile, für jedes eigene Target: `llm-cli zoo export target my_board --author "<dein Name>"`.

   Das Paket wird nach `community/contributions/contribution-target-my_board-1.0.0.zip` geschrieben und mit deinem
   Schlüssel signiert, der beim ersten Mal im Secrets-Speicher angelegt wird.
3. **Die Zusammenfassung lesen.** Sie zeigt einen Diff für jede Datei, die die Datenschutz-Bereinigung geändert hat
   (Home-Ordner, IP- und MAC-Adressen, E-Mail-Adressen, Host- und Benutzernamen, Schlüssel; höchstens 40 Zeilen je
   Datei), und Hinweise, zum Beispiel dass `knowledge/` Seiten aus dem Web enthält, deren Bedingungen die Lizenz des
   Pakets nicht abdeckt. Macht eine Änderung ein Skript
   kaputt, korrigiere die Datei in deinem Target und exportiere erneut.
4. **So prüfen, wie das Community-Repository prüfen wird:**
   `llm-cli zoo verify community/contributions/contribution-target-my_board-1.0.0.zip`.
5. **Einreichen:** „Open pull request“ im Dialog oder der Export-Befehl mit zusätzlichem `--pr`. Was dann passiert
   und wie der aktuelle Stand des Community-Repositorys ist, steht in
   [CONTRIBUTING](../CONTRIBUTING_DE.md#beiträge-zum-community-zoo).

Um ein Target nur mit deinen eigenen Rechnern oder einem Team zu teilen, verschlüssele es stattdessen:
`--to <Schlüsseldatei>` oder `--passphrase` ([CLI-Anleitung](CLI_Usage_DE.md#zoo-export)). Der Empfänger installiert
es mit `llm-cli zoo install --file`. Ein Target, das du aus dem Zoo installiert hast, lässt sich nicht als deines
exportieren: Es bleibt das Paket seines Autors.

## 10. Altlasten

Version 1.x nutzte je Target eine Kette aus vier Skripten (`source_module.sh`, `config_module.sh`,
`convert_module.sh`, `target_module.sh`), gesteuert von der Hardware-Probe. Die aktuelle Pipeline ruft nur `build.sh`
auf; das Rockchip-`build.sh` nutzt diese Skripte nicht, und der Modulgenerator schreibt sie noch. Ihre Entfernung und
eine überarbeitete Vorlage sind für 3.4.0 geplant.
