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
6. [Die Stufen des Builds](#6-die-stufen-des-builds)
7. [Regeln der Sandbox](#7-regeln-der-sandbox)
8. [Ein Target testen](#8-ein-target-testen)
9. [Ein Target teilen](#9-ein-target-teilen)
10. [Was die Stufenmodule aus Version 1.x behalten haben](#10-was-die-stufenmodule-aus-version-1x-behalten-haben)
11. [Die Datei der Hardware-Probe](#11-die-datei-der-hardware-probe)

## 1. Wie das Framework ein Target nutzt

1. **Laden.** Beim Start liest der `TargetManager` die `target.yml` in jedem Ordner unterhalb von `targets/`.
   Ordner, deren Name mit `_` oder `.` beginnt (etwa `_template`), werden übersprungen, ebenso eine `target.yml`
   ohne Block `metadata:`. Die Target-ID ist `metadata.id`, wenn gesetzt, sonst der Ordnername. CLI,
   Build-Service, Orchestrator und Build-Engine lösen ein Target über eine einzige Zuordnung von ID zu Ordner auf;
   `llm-cli build start -t` nimmt daher die ID oder den Ordnernamen (in beliebiger Groß- und Kleinschreibung), und
   der Build nutzt in beiden Fällen den richtigen Ordner.
2. **Capabilities.** Der Block `capabilities:` wird gegen das Schema in `orchestrator/schemas/targets.py` geprüft.
   Die GUI bietet genau die dort aufgeführten Formate, Boards und Quantisierungen an, die CLI lehnt alles andere ab,
   und die Tools des MCP-Servers und Dittos starten Builds nur für Targets mit gültigem Block. Ein ungültiger Block
   wird als Fehler protokolliert, und das Target wird dann behandelt, als hätte es keinen.
3. **Image-Build.** Für jeden Build stellt die `BuildEngine` einen Build-Kontext zusammen (`modules/` und
   `scripts/` des Targets, das gemeinsame `entrypoint.sh` als `docker/entrypoint.sh` und das Dockerfile) und baut
   das Image `llm-framework/<Target>:<Build-ID>`. Dieser Schritt hat Netzwerk.
4. **Stufen.** Zuerst wird das Modell auf dem Host bereitgestellt. Dann läuft `modules/build.sh`, der Stage-Runner,
   in abgeschotteten Containern ohne Netzwerk: einmal mit `JOB_TYPE=imatrix`, wenn für GGUF eine Importance-Matrix
   gewünscht ist, dann mit `JOB_TYPE=build`. Er führt die vier Stufenmodule des Targets in dieser Reihenfolge aus:
   `source`, `config`, `convert` und `target` ([Abschnitt 6](#6-die-stufen-des-builds)); der imatrix-Auftrag endet
   nach `convert`.
5. **Ergebnisse.** Dateien, die die Stufen in den Ausgabeordner schreiben, werden nach `output/` kopiert. Die Engine
   ergänzt `build_info.json` um Größe und SHA256-Hash jeder Modelldatei und schreibt `Model_Card.md` und ein ZIP.
   Das Deployment findet Builds über dieses Manifest.

## 2. Ordneraufbau

```text
targets/Rockchip/
├── target.yml           Metadaten und Capabilities
├── dockerfile           Build-Umgebung („Dockerfile“ geht ebenso)
├── modules/
│   ├── build.sh           der Stage-Runner: der Einstieg jedes Stufen-Containers
│   ├── source_module.sh   1. source: die Quellen der Werkzeuge im Image
│   ├── config_module.sh   2. config: Prüfung des Auftrags und der Build-Plan
│   ├── convert_module.sh  3. convert: die Umwandlung des Modells
│   ├── target_module.sh   4. target: Quantisierung und die Ergebnisse
│   ├── rkllm_module.sh    RKLLM-Export (RK3588, RK3576), ein Format-Helfer der Stufe convert
│   └── rknn_module.sh     RKNN-Umwandlung, ein Format-Helfer der Stufe convert
└── scripts/
    ├── export_rkllm.py
    └── rknn_converter.py
```

| Datei | Nötig | Wofür |
|---|---|---|
| `target.yml` mit Block `metadata:` | ja | Ohne ihn ist der Ordner kein Target. |
| Block `capabilities:` in `target.yml` | dringend empfohlen; Pflicht, um das Target zu teilen | Ohne ihn können GUI und CLI keine Einstellungen prüfen, die MCP-Tools und Ditto keine Builds für das Target starten, `llm-cli mojo` warnt, und das Target lässt sich nicht als Community-Paket exportieren oder installieren. |
| `Dockerfile` oder `dockerfile` | ja | Das Image, in dem alle Stufen laufen. |
| `Dockerfile.gpu` | optional | Für Builds mit durchgereichter GPU (`llm-cli build start --gpu` oder „Use Host GPU for Build Process“ in der GUI, unter Linux standardmäßig angehakt); ohne diese Datei wird das normale Dockerfile genutzt. |
| `modules/build.sh` | ja | Der Stage-Runner; läuft in jedem Stufen-Container als `/app/modules/build.sh`. Übernimm ihn unverändert aus der Vorlage. |
| `modules/source_module.sh`, `config_module.sh`, `convert_module.sh`, `target_module.sh` | ja | Die vier Stufen des Builds ([Abschnitt 6](#6-die-stufen-des-builds)). |
| `modules/rotate_module.sh` | optional | Ein Rotationsschritt zwischen convert und target (Roadmap); läuft nur, wenn die Datei da ist. |
| `modules/benchmark_module.sh` | optional | Läuft in keinem Build: Es misst den Build-Rechner, und ein Modell für ein Board wird auf dem Board gemessen. |
| `scripts/` | optional | Hilfsskripte; werden neben `modules/` ins Image kopiert. |

## 3. Ein Target anlegen

### 3.1 Das Board untersuchen

Die Probe läuft auf dem Gerät, weil der Build aus ihrem Ergebnis geplant wird. Per SSH:

```bash
llm-cli scan --device 192.0.2.10 --user dietpi
```

Die CLI prüft beim ersten Mal den Hostschlüssel des Geräts (wie das Deployment), schickt `hardware_probe.py` an
`python3 -I`, wenn das Gerät Python 3.8 oder neuer hat, sonst `hardware_probe.sh`, schreibt
`target_hardware_config.txt` und behält das Profil unter der ID des Geräts
([Abschnitt 11.5](#115-wo-profile-liegen)). Auf dem Gerät wird nichts installiert. Ohne SSH kopierst du eine Probe auf
das Gerät, führst sie dort aus und bringst die Datei zurück:

```bash
python3 -I hardware_probe.py                                 # Python 3.8 oder neuer
sh hardware_probe.sh                                         # Linux ohne Python, jede POSIX-Shell
powershell -ExecutionPolicy Bypass -File hardware_probe.ps1  # Windows ohne Python
```

Die Datei enthält das Betriebssystem (ID, Version, Familie, Kernel, Init-System), die Architektur, die C-Bibliothek
und ihre Version, CPU-Modell, Kerne, Cluster und Merkmale, Speicher, SoC, NPU und GPUs; keinen Hostnamen, keine
Seriennummer und keine MAC-Adresse. Die Probes fragen nichts und installieren nichts; `hardware_probe.sh
--install-tools` bietet zuerst an, `vulkan-tools` zu installieren, und `hardware_probe.ps1 -Elevate` startet sich mit
Administratorrechten neu. Das Format beschreibt [Abschnitt 11](#11-die-datei-der-hardware-probe).

Im Build-Reiter der GUI führt **Scan / Import... > Remote Scan via SSH...** die Shell-Probe per SSH auf einem Gerät
aus (mit derselben Prüfung beim ersten Kontakt) und holt das Ergebnis ab. `llm-cli scan` ohne `--device` untersucht
den Rechner, an dem du arbeitest.

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
  `riscv64`), SDK, Basis-Image, Beschreibung, Paketen und CPU-Flags (nur als Hinweis, siehe
  [Abschnitt 3.4](#34-was-der-generator-schreibt-und-was-du-ergänzt)). Mit `--probe <Probe-Datei>` (dem Profil, das
  `llm-cli scan --device` schreibt) wird der `runtime:`-Block des Moduls aus der Probe des Boards geplant; eine Datei,
  die kein Probe-Profil ist, wird abgelehnt. Führe den Befehl im Installationsordner aus: Anders als die übrigen
  Befehle löst er ein relatives `targets_dir` gegen den aktuellen Ordner auf.
- **CLI mit Ditto:** `llm-cli module generate-ai target_hardware_config.txt` (auf Wunsch mit `--provider` und
  `--model` für diesen Lauf). Ditto liefert einen strukturierten Vorschlag (Architektur, SDK, Basis-Image, Pakete,
  CPU- und CMake-Flags, Einrichtungsbefehle, Quantisierungsblock). Ist die Wissensbasis eingeschaltet, zieht es
  vorher passende Dokumentationsauszüge hinzu. Der Vorschlag wird vollständig angezeigt; geschrieben wird erst nach
  deiner Bestätigung. Leerzeichen im Modulnamen werden zu Unterstrichen. Ist die Probe-Datei ein Probe-Profil, wird
  der `runtime:`-Block daraus geplant, wie bei der im Assistenten eingelesenen Probe-Datei.

### 3.3 Was der Generator prüft

Jeder Wert landet in einem Dockerfile, in YAML oder in einem Shell-Skript; deshalb wird er geprüft, bevor eine
Vorlage ausgefüllt wird:

- **Name:** 1 bis 64 Buchstaben, Ziffern, Leerzeichen, `_`, `.` oder `-`, beginnend mit Buchstabe oder Ziffer. Der
  Ordnername ist der kleingeschriebene Name mit Unterstrichen (aus `My Board` wird `targets/my_board/`).
- **Architektur:** eine von `aarch64`, `x86_64`, `armv7l`, `riscv64`.
- **Basis-Image:** muss per Digest festgelegt sein (`image@sha256:...`), außer es beginnt mit `debian:`, `ubuntu:`,
  `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` oder `python:`. Die vorgebauten CUDA-Images von NVIDIA sind nicht
  ausgenommen: Für die GPU-Vorlage ist ein per Digest festgelegtes `nvidia/cuda:<Version>-devel-ubuntu22.04`-Image
  der ausdrückliche Opt-in aus [Abschnitt 5.2](#52-cuda-in-der-gpu-vorlage); jedes andere `nvidia/cuda`-Image wird
  abgelehnt. Ein vorgebautes llama.cpp-Image wird ebenfalls abgelehnt.
- **Pakete:** gültige Debian-Paketnamen. **Flags:** einfache Compiler- oder CMake-Optionen wie `-mcpu=cortex-a76`
  oder `-DGGML_CUDA=ON`. Sie sind nur Hinweise: Das Modul vermerkt sie in seinem `runtime:`-Block, und kein Build
  wendet sie je an. Die CMake-Flags dürfen keine Option von llama.cpp einschalten, die beim Bauen etwas herunterlädt
  (`-DLLAMA_USE_PREBUILT_UI=ON`, `-DLLAMA_CURL=ON`, `-DGGML_CUDA_CCCL_VERSION=...`,
  `-DFETCHCONTENT_FULLY_DISCONNECTED=OFF`) oder `llama-server` Unterprozesse gibt (`-DLLAMA_SUBPROCESS=ON`): Builds
  haben kein Netzwerk, und das Board-Paket eines von Hand übergebenen Hardware-Profils nimmt diese Flags. Sie werden
  mit dem Grund des Build-Rezepts abgelehnt (`refused_option` in `orchestrator/runtime/build/recipe.py`, die Regel,
  der jeder Build-Plan folgt). Sie dürfen CMake auch kein Skript ausführen lassen (`-C`, einen Initial-Cache, oder
  `-P`): Ein Skript kann jede Option setzen.
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
`requirements`, einen Block `capabilities:` und einen Block `runtime:`), ein `Dockerfile` (aus dem `Dockerfile.gpu` der Vorlage, wenn das
SDK „cuda“ oder das Basis-Image „nvidia“ enthält), den Stage-Runner `modules/build.sh`, die vier Stufenmodule
`modules/source_module.sh`, `config_module.sh`, `convert_module.sh` und `target_module.sh` (mit dem Namen des Moduls,
seiner Architektur und seinem Basis-Image in ihren Köpfen), `modules/rkllm_module.sh`, `modules/rknn_module.sh`,
`modules/benchmark_module.sh`, `generate_profile.sh` und die `scripts/` der Vorlage. Die Felder des Assistenten
landen in der Stufe, zu der sie gehören: die Einrichtungsbefehle in der Stufe source, die Quantisierungslogik in der
Stufe convert und die Packbefehle in der Stufe target.

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

**Der Runtime-Block.** Die Hardware-Probe läuft auf dem Target, und das Build-Rezept
(`orchestrator/runtime/build/recipe.py`) leitet daraus ab, wie die llama.cpp-Laufzeit des Boards (`llama-server`,
`llama-cli`, `llama-bench`, `llama-perplexity`) aus dem festgelegten Commit gebaut wird. Der Generator schreibt eine
Zusammenfassung dieses Plans in `target.yml`. Mit der Probe eines Radxa ZERO 3W (RK3566, DietPi) und den Flags, die
ein Assistent vorgeschlagen hat, sieht er so aus:

```yaml
runtime:
  schema_version: 1
  built_from: source
  recipe: orchestrator/runtime/build/recipe.py
  purpose: device-runtime
  arch: arm64
  os: linux
  profile_perf_key: 2beb8057d73cf1dcc0a5db4f830650d61814ef9b953159f42733c224929fd833
  libc: glibc
  libc_min: glibc-2.36
  placement: cross
  builder_base: debian-12
  cpu: explicit
  cpu_arm_arch: armv8.2-a+dotprod+fp16
  backends: [cpu]
  targets: [llama-server, llama-cli, llama-bench, llama-perplexity]
  advisory:
    cpu_flags: -mcpu=cortex-a55
    cmake_flags: -DGGML_NEON=ON
    note: "proposed in the module wizard or by an AI; never applied: ..."
```

- **Woher die Probe kommt:** die im Assistenten eingelesene Probe-Datei, die Probe-Datei von `module generate-ai`
  oder `llm-cli module create --probe <Datei>`. Ohne Probe enthält der Block die Architektur, `backends: [cpu]` und
  einen Hinweis, dass die Laufzeit beim Bauen aus der Probe des Boards geplant wird (`llm-cli scan --device`).
- **Was die Felder bedeuten:** `libc_min` ist die C-Bibliothek des Geräts, der Cross-Build nutzt also eine
  Builder-Basis, die nicht neuer ist (`builder_base`, aus `build_bases` in `configs/llama_runtime.yml`);
  `cpu_arm_arch` (ARM) oder `cpu_features` (x86) sind die CPU-Optionen aus der Probe, `cpu: all-variants` heißt, die
  Probe konnte es nicht sagen und jede CPU-Variante wird gebaut, `cpu: native` ein Build auf dem Gerät selbst;
  `backends` die Builds, die sich lohnen (der CPU-Build zuerst); `profile_perf_key` kennzeichnet die Probe für
  Benchmark-Ergebnisse; `notes` sagen, was das Rezept weggelassen hat und warum.
- **Eine Aufzeichnung, keine Eingabe:** Die Geräte-Laufzeit wird beim Bauen immer neu aus der eigenen Probe des
  Geräts geplant; ein geänderter Block, auch einer in einem Community-Paket, ändert also keinen Build. Der Block eines
  Pakets muss trotzdem zum Schema passen (`TargetRuntime` in `orchestrator/schemas/targets.py`).
- **Flags sind nur Hinweise:** CPU- und CMake-Flags aus dem Assistenten oder aus Dittos Vorschlag bleiben unter
  `advisory` lesbar und werden von keinem Build angewendet; `config_module.sh` nutzt sie nur für ein von Hand
  angegebenes Hardware-Profil (Abschnitt [10](#10-was-die-stufenmodule-aus-version-1x-behalten-haben)).

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
die Lizenz eines Pakets deckt sie nicht ab. Seiten, die mit vollem Text unter einer Lizenz bleiben, die ihren Text oder
Erlaubnishinweis verlangt (MIT, BSD, ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA), bekommen den kanonischen Lizenztext
in `knowledge/LICENSES/<SPDX-ID>.txt` und eine `knowledge/NOTICE`, die jede Seite mit Quelladresse, Copyright-Zeile
und Lizenz aufführt; die Platzhalterprüfung lässt auch diese aus, und ein Export erstellt sie neu aus dem
Schnappschuss.

Das Ergebnis ist ein **Gerüst**, noch kein funktionierendes Target:

- **Der Capabilities-Block ist ein Ausgangspunkt.** Ergänze deine echten Boards, weitere Formate, Quantisierungen je
  Board und den Bedarf an Kalibrierdaten ([Abschnitt 4](#4-targetyml-und-der-capabilities-block)), und halte den
  Block im Einklang mit `config_module.sh` (Namen von Format, Board und Typ).
- **Die Stufen der Vorlage folgen den Rockchip-Stufen** (`targets/Rockchip/modules/`), und ihr `build.sh` ist
  derselbe Stage-Runner: `convert_hf_to_gguf.py` und `build/bin/llama-quantize` / `llama-imatrix` von llama.cpp unter
  `LLAMA_CPP_PATH` (`/usr/src/llama.cpp`), die F16-Zwischendatei im Arbeitsordner (`BUILD_SCRATCH_DIR`, nie `/tmp`),
  dieselben GGUF-Typlisten und Regeln für die Importance-Matrix sowie der Auftrag `imatrix`. Die Zweige deines
  Quantisierungsblocks laufen zuerst, in der Stufe convert; ein Zweig kann `build_gguf <TYP>` oder
  `convert_to_gguf <Ausgabetyp> <Datei>` aufrufen, und jeder andere Typ läuft über `build_gguf`, das Typen ablehnt,
  die llama-quantize nicht kennt. `build_gguf` überlässt das Quantisieren der Stufe target. Nichts wird unverändert
  durchkopiert, und ein Build, der kein Artefakt schreibt, scheitert.
- **Einrichtungsbefehle laufen einmal,** in der Stufe source. Jede Stufe ist ein eigener Prozess: Was die Befehle
  exportieren, erreicht die Stufen convert und target (über `sdk_environment.sh` im Stufenordner, auch wenn ein Build
  fortgesetzt wird), eine nur gesetzte Variable nicht.
- **Das Vorlagen-Dockerfile baut die Konvertierungswerkzeuge von llama.cpp** mit denselben Schritten wie
  `targets/Rockchip/dockerfile` ([Abschnitt 5.1](#51-llamacpp-im-image-rezepte-keine-binärdateien)): den
  festgelegten Commit in `/usr/src/llama.cpp`, `llama-quantize`, `llama-imatrix`, `llama-perplexity`,
  `llama-completion` und `llama-bench`, abgestimmt auf den Build-Rechner, und die Abhängigkeiten des Konverters.
  Behalte diese Schritte, wenn du das Dockerfile änderst.

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
  # id: optional; ohne sie ist der Ordnername die Target-ID (siehe Abschnitt 1)
```

### 4.2 capabilities

Der Block ist die einzige Quelle dafür, was das Target bauen kann. GUI, CLI, Build-Service, Ditto und der
MCP-Server leiten ihre Auswahl und ihre Prüfungen daraus ab.

```yaml
capabilities:
  schema_version: 1
  support_level: community
  default_board: my_board        # das Board, das die Stufe config ohne Angabe nutzt
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
| `default_board` | Das Board, das der Build nutzt, wenn ein Auftrag keins nennt. Muss in `boards` stehen und zur Vorgabe in deinem `config_module.sh` passen. |
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
Deployment-Pakets mit Docker-Images. Der Block `runtime:` erzeugter Module
([Abschnitt 3.4](#34-was-der-generator-schreibt-und-was-du-ergänzt)) zeichnet den Plan des Rezepts auf und wird gegen
sein Schema geprüft, aber kein Build liest ihn. Die übrigen Blöcke in der Rockchip-`target.yml` (`supported_boards`,
`docker`, `modules`, `optimization_profiles`, `requirements` usw.) sind beschreibend: `modules` führt den
Stage-Runner, die vier Stufenmodule und die Format-Helfer auf, und `docker.build_args` wiederholt nur den Pin für
Builds außerhalb des Frameworks.

`docker.image_name`, `docker.registry` und `docker.tags` sind **nur lokale Image-Namen**. Kein Framework-Image wird
veröffentlicht oder geladen: Die Build-Engine baut das Image jedes Targets auf deinem Computer und versieht es lokal
mit dem Tag `llm-framework/<Target>:<Build-ID>`, und nichts liest `registry` oder `tags` (die Werte `ghcr.io` und
`latest`/`v1.0.0` in der Rockchip-Datei und der Vorlage sind Überbleibsel, die nur einen Namen beschreiben). Lade
Target-Images nicht aus dem Framework heraus in eine Registry hoch, und verlasse dich für nichts auf diese Schlüssel.

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
| `LLAMA_CPP_TAG`, `LLAMA_CPP_COMMIT` | der llama.cpp-Pin in `configs/llama_runtime.yml` (`b11429`, `d81235049384534c167caea52b85a694f6103d14`). Die Einstellung `llama_cpp_version` kann sie nicht verschieben: Ein anderer Wert ergibt nur eine Warnung im Build-Log |
| `LLAMA_CMAKE_ARGS` | nur wenn das Dockerfile `ARG LLAMA_CMAKE_ARGS` deklariert: der Plan des Build-Rezepts für die Konvertierungswerkzeuge auf diesem Rechner, als `-DNAME=WERT`-Wörter ([Abschnitt 5.1](#51-llamacpp-im-image-rezepte-keine-binärdateien)) |
| `BUILD_JOBS` | die Einstellung `build_jobs`, nur wenn sie größer als 0 ist; das Self-Healing senkt sie auf 1, wenn einem Compiler der Speicher ausging |
| `USER_ID`, `GROUP_ID` | die IDs des Host-Benutzers (Linux und macOS, wenn der Host-Benutzer nicht root ist) |

Die drei llama.cpp-Argumente kommen nur aus dem Pin und dem Rezept: Build-Argumente, die ein Build-Auftrag
mitbringt, können sie nicht ersetzen.

Was das Image bieten muss (siehe `targets/Rockchip/dockerfile`):

- `/app/modules/build.sh` und die Stufenmodule daneben, ausführbar (`COPY modules/ ./modules/` und `chmod +x`).
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

### 5.1 llama.cpp im Image: Rezepte, keine Binärdateien

Das Framework teilt Rezepte, nie fertig gebaute Images oder Programme. Ein Target baut llama.cpp aus dem Quelltext mit
den gemeinsamen Schritten aus `targets/Rockchip/dockerfile`, die die Vorlage unverändert übernimmt:

1. **Per Commit holen.** `LLAMA_CPP_COMMIT` (40 Hex-Zeichen) und `LLAMA_CPP_TAG` (`b` und eine Zahl) werden geprüft,
   der Commit wird mit `git fetch --depth 1 origin <Commit>` geholt (der Tag nur, wo ein Server das ablehnt), und der
   Build bricht ab, wenn `git rev-parse HEAD` nicht genau diesen Commit zurückgibt. Ein verschobener Tag stoppt den
   Build also. `.git` wird danach entfernt; `.llama_cpp_tag` und `.llama_cpp_commit` halten den Pin im Image fest.
2. **Mit dem Plan konfigurieren.** Jedes Wort von `LLAMA_CMAKE_ARGS` muss wie `-DNAME=WERT` aussehen (nur Buchstaben,
   Ziffern und `. _ : + / $ ; -`), `GGML_NATIVE` muss gesetzt sein, und Optionen, die bei jedem Setzen etwas
   herunterladen (`LLAMA_BUILD_BORINGSSL`, `GGML_CUDA_CCCL_VERSION` und die übrigen des Rezepts), werden abgelehnt.
   Nach diesen Wörtern setzt die Befehlszeile, was jeder Build hat: Tests, Beispiele, die App, den Server, die
   Web-Oberfläche, llguidance, KleidiAI und den OpenMP-Download aus, `LLAMA_OPENSSL=OFF`, `LLAMA_SUBPROCESS=OFF`,
   `FETCHCONTENT_FULLY_DISCONNECTED=ON` sowie Build-Nummer und Commit für `--version`. CMake nimmt den letzten Wert,
   diese gewinnen also immer. Ein Konfigurationsprotokoll, das einen Download meldet, stoppt den Build.
3. **Bauen und prüfen.** `llama-quantize`, `llama-imatrix`, `llama-perplexity`, `llama-completion` und `llama-bench`
   werden gebaut, und `llama-bench --version` muss den festgelegten Build und Commit melden.

**Abgestimmt auf den Build-Rechner.** Das Image läuft auf dem Rechner, der es baut, deshalb übergibt die Engine den
Plan des Rezepts für den Zweck `host-tools` aus einer Probe dieses Rechners, unmittelbar vor dem Image-Build erstellt
(oder, wenn das scheitert, aus dem Profil, das `llm-cli scan` gespeichert hat): die CPU-Merkmale ausgeschrieben (etwa
`-DGGML_AVX512=ON` nur, wo die CPU AVX-512 hat), oder alle CPU-Varianten, von denen zur Laufzeit die beste gewählt
wird, wenn die Probe nicht jedes Merkmal erkennen konnte. Ohne Probe, oder wenn der Docker-Daemon auf einem anderen
Rechner läuft, entfällt das Argument und die Vorgabe des Dockerfiles gilt: die feste x86-64-Basis, die diese Werkzeuge
immer hatten (AVX2, FMA, F16C, kein AVX-512), statisch gelinkt, ausgeschrieben. Das Build-Log sagt, was gilt, und
`build_info.json` hält es unter `llama_cpp_tools` fest (Tag, Commit, Abstimmung, ID und Wörter des Plans). CPU- und
CMake-Flags aus dem Modul-Assistenten oder von einer KI erreichen diesen Build nie.

**Was die Prüfungen ablehnen** ([Abschnitt 9.1](#91-was-die-prüfungen-verlangen)): ein Image, das nach llama.cpp oder
`llama-server` benannt ist (auch per Digest festgelegt), oder eines der früheren Laufzeit-Images des Frameworks,
llama.cpp-Programme, die aus einem anderen Image kopiert werden, Downloads von llama.cpp-Release-Archiven oder
-Programmen, Paketquellen mit vorgebauten `llama-cpp-python`-Wheels und llama.cpp-Pakete einer Distribution
(`apt-get install llama.cpp` und Ähnliches).

### 5.2 CUDA in der GPU-Vorlage

`targets/_template/Dockerfile.gpu` baut in beiden Stufen auf `ubuntu:22.04`, per Digest festgelegt, und nimmt CUDA
12.9.1 aus den Redist-Archiven von NVIDIA: `redistrib_12.9.1.json` ist über seine sha256 festgelegt (die Werte von
`cuda.toolkits` in `configs/llama_runtime.yml`), und jede Komponente (`cuda_nvcc`, `cuda_cudart`, `cuda_cccl`,
`libcublas`) wird gegen die sha256 aus dem Manifest geprüft, bevor sie nach `/usr/local/cuda` entpackt wird. Die
Laufzeitstufe bekommt die CUDA-Laufzeitbibliotheken (cudart, cuBLAS). Die Komponenten stehen unter der Lizenz des
CUDA Toolkit von NVIDIA (LICENSE in jedem Archiv); der Bau des Images lädt sie für deinen eigenen Gebrauch herunter.
Die llama.cpp-Konvertierungswerkzeuge bleiben CPU-Builds; GPU-Werkzeuge sind ein späterer Schritt des Rezepts.

Das vorgebaute Entwicklungs-Image von NVIDIA ist ein ausdrücklicher Opt-in: Wähle
`nvidia/cuda:12.9.1-devel-ubuntu22.04@sha256:bd4e2680a261c212f1e2fea241606f71497dc67a417f73175d794ec8212b5ba8` (oder
ein anderes per Digest festgelegtes `-devel-ubuntu22.04`-Image) selbst als Basis-Image im Assistenten oder bei
`module create`. Dittos Vorschläge enthalten nie ein `nvidia/cuda`-Image: Ein Vorschlag mit einem solchen geht einmal
an Ditto zurück und wird sonst abgelehnt, weil eine Probe-Datei den Vorschlag lenken könnte. Der Generator nutzt das
Image dann für die Builder-Stufe und setzt `CUDA_SOURCE=prebuilt-image`, das das Image als Label
`org.llm-framework.cuda-source` trägt; das Toolkit wird dann nicht aus dem festgelegten Manifest bereitgestellt, und
das Log des Generators und das Log des Image-Builds sagen „not self-built“. Die Laufzeitstufe bleibt auf dem
festgelegten Ubuntu 22.04, weil die Python-Umgebung von einer Stufe in die andere kopiert wird.

### 5.3 Basis-Images und Hersteller-Software KI-erstellter Module

Ein Modul, das eine KI vorschlägt (Ditto bei `module generate-ai` und im Assistenten oder ein MCP-Client über
`create_target_module`), darf von **jedem Basis-Image** ausgehen, weil manche Hersteller ihre Toolchain nur als
Container-Image ausliefern (zum Beispiel die Jetson/L4T-Images von NVIDIA auf `nvcr.io`). Die Regeln:

- **Per Digest festgelegt.** Ein Tag wird beim Nachfragen in den Digest aufgelöst, auf den es gerade zeigt (anonym
  über die öffentliche Schnittstelle der Registry; im Offline-Modus abgelehnt; eine Registry, die eine Anmeldung
  verlangt, wird nicht genutzt, gib das Image dann als `image@sha256:...` an), und die Builder-Stufe des Dockerfiles
  beginnt mit genau diesem Digest; der Generator prüft, dass die Builder-Stufe mit dem Basis-Image des Moduls
  beginnt, auch bei der GPU-Vorlage. Registries mit Port (`registry.example:5000/img@sha256:...`) sind erlaubt.
- **Klar gezeigt.** Ein Docker Official Image (`debian`, `ubuntu`, `python`, ohne Namensraum) bekommt die normale
  Frage. Jedes andere Image wird gezeigt als „other registry: <Registry>; prebuilt image made by its publisher, not
  checked by the framework“, mit „the image build runs with network access on this computer, outside the build
  sandbox“, den Plattformen in seinem Manifest und ob eine zu diesem Rechner passt (ein reines arm64-Image auf einem
  x86_64-Rechner wird gezeigt, nicht abgelehnt). Ein solches Image braucht `base_image_reason`; besser ist ein
  offizielles Image mit den Paketquellen des Herstellers.
- **Hersteller-Software**, die die Dockerfile-Vorlage des Frameworks beim Image-Build einfügt (die Shell-Felder der
  KI dürfen weiterhin nichts herunterladen):
  - `package_sources`: eine https-Paketquelle für apt (`url`, `suite`, `components`) mit ihrem öffentlichen
    Signaturschlüssel (`signed_by_url`) und dem vollen Fingerabdruck des Schlüssels (`signed_by_fingerprint`). Der
    Image-Build holt den Schlüssel, bricht ab, wenn sein Fingerabdruck abweicht, und trägt die Quelle mit
    `signed-by` ein; `packages` kann dann ihre Pakete nennen.
  - `pip_packages`: Python-Pakete, genau festgelegt als `name==version`, von PyPI oder aus einem https-Index
    (`pip_index_url`), installiert in die Python-Umgebung des Images.
  - `pinned_downloads`: Dateien mit https-Adresse, Dateiname (`name`), genauer Größe (`size_bytes`) und `sha256`; der
    Image-Build prüft Größe und Prüfsumme, bevor die Datei ihren Namen in `/opt/vendor` bekommt (wird in die
    Laufzeitstufe kopiert), wo `setup_commands` sie entpacken kann.
  Die Sicherheitsprüfungen sind hart (https, genaue Versionen, Größe und sha256, Fingerabdrücke aus 40 Hex-Zeichen,
  nie ein vorgebautes llama.cpp-Paket, ein Wheel-Index oder eine Binärdatei). Die Anzahlen sind weiche Grenzen
  (3 Quellen, 30 pip-Pakete, 5 Downloads): Darüber zu gehen ist eine Warnung in der Frage, über die die Zustimmung
  der Person hinweggeht, nie eine Ablehnung.
- **Ungetestet wird gesagt.** Die neue `target.yml` bekommt `metadata.provenance: ai-generated`,
  `metadata.untested: true`, die `assumptions` und den `base_image_reason` der KI und die Support-Stufe `community`;
  Builds davon warnen. KI-erstellte Module bekommen nie eine dauerhafte Freigabe: Jedes bestätigt die Person.

## 6. Die Stufen des Builds

Ein Build besteht aus vier Stufenmodulen in `modules/`. `modules/build.sh`, der **Stage-Runner**, ist der Einstieg
jedes Stufen-Containers (`/app/modules/build.sh`, in der Sandbox aus [Abschnitt 7](#7-regeln-der-sandbox)); er
führt die Stufen in dieser Reihenfolge aus und tut sonst nichts (Entscheidung U9 des Maintainers). Der Kopf jedes
Moduls sagt, in welchem Image es läuft, für welches System sein Ergebnis ist und was seine Stufe tut; halte diese
Köpfe wahr, wenn du eine Stufe änderst.

| Stufe | Modul | Was sie tut (Rockchip) |
|---|---|---|
| 1. source | `source_module.sh` | Nennt die Quellen der Werkzeuge, aus denen das Image gebaut wurde (Tag und Commit von llama.cpp, die das Dockerfile geprüft hat, die SDK-venvs); in der Vorlage führt sie außerdem einmal die Einrichtungsbefehle der Moduldefinition aus. Nichts wird heruntergeladen. |
| 2. config | `config_module.sh` | Liest den Auftrag aus der Umgebung, vereinheitlicht die Namen von Format, Board und Typ, lehnt ab, was das Target nicht bauen kann, und schreibt den Build-Plan `build_config.sh` in den Stufenordner. Mit einem Hardware-Profil plant sie außerdem das Board (Toolchain und Flags) und prüft diesen Plan, bevor sie ihn behält ([Abschnitt 10](#10-was-die-stufenmodule-aus-version-1x-behalten-haben)). |
| 3. convert | `convert_module.sh` | Wandelt das Modell um: GGUF-Typen, die der Konverter selbst schreibt, landen direkt im Ausgabeordner; jeder andere Typ wird zu einer F16-Zwischendatei im Arbeitsordner für die Stufe target. RKLLM und RKNN führen ihre Format-Helfer `rkllm_module.sh` und `rknn_module.sh` aus. Im Auftrag `imatrix` berechnet sie die Importance-Matrix. Um jede Umwandlung herum erkennt sie das Format der Eingabe, prüft die Eingabe und was der Konverter geschrieben hat, führt einen kurzen Funktionstest aus und hält eine Statistik fest ([Abschnitt 6.5](#65-die-prüfungen-der-stufe-convert)). |
| (rotate) | `rotate_module.sh` | Optionaler Haken zwischen convert und target für eine Rotation wie SpinQuant oder QuaRot (Roadmap): läuft nur, wenn das Target diese Datei hat; mitgeliefert wird keine. Sie darf die Zwischendatei in voller Genauigkeit ändern, bevor sie quantisiert wird. |
| 4. target | `target_module.sh` | Quantisiert, was die Stufe convert für sie hinterlassen hat (`quantize_jobs.sh`), entfernt die Zwischendatei, schreibt `build_info.json` und listet die Ergebnisse auf. Für einen GGUF-Build mit Hardware-Profil und mit `build.sh --package` macht sie außerdem das Paket für das Board ([Abschnitt 10](#10-was-die-stufenmodule-aus-version-1x-behalten-haben)). |

Der Auftrag `imatrix` führt source, config und convert aus; der Auftrag `build` alle Stufen. Ein von Hand
gestartetes Stufenmodul (`bash modules/convert_module.sh`) führt über den Runner den Build bis zu seiner Stufe aus;
`build.sh --to STUFE` tut dasselbe (ein von Hand gestartetes `target_module.sh` fragt außerdem nach dem Paket:
`build.sh --to target --package`). Eine spätere Stufe darf die Funktionen einer früheren nutzen, indem sie deren
Modul einliest (`config_module.sh` und `source_module.sh` führen ihr `main` nur in ihrer eigenen Stufe aus).
`benchmark_module.sh` ist keine Stufe: Es würde die CPU des Build-Rechners messen, und ein Modell für ein Board wird
auf dem Board gemessen (`llm-cli device runtime verify --bench-model` oder **Boards > Boards and Devices... >
Verify...**).

**Fortsetzen.** Wenn eine Stufe fertig ist (Exit-Code 0), schreibt der Runner eine Marke nach
`$BUILD_SCRATCH_DIR/stages/<Auftrag>/` mit einer Prüfsumme der Eingaben der Stufe und der Größe jeder Datei, die die
Stufe mit `stage_output DATEI` genannt hat. Die Eingaben sind der Auftrag (Format, Board, Quantisierung, Modellordner
mit Größe und Zeit seiner Dateien, Revision und Name des Modells, Kontextlänge, Importance-Matrix,
Kalibrierdatensatz, Hardware-Profil, auch das, das die Stufe config liest, wenn keins genannt ist, Ausgabeordner),
die Werkzeuge (der llama.cpp-Pin des Images, die SDK-Pfade) und der SHA-256
jeder Datei in `modules/` und `scripts/`. Erneut auf demselben Arbeitsordner gestartet, überspringt der Runner jede
Stufe, deren Marke dieselbe Prüfsumme hat und deren Dateien noch da sind; läuft eine Stufe erneut, laufen auch alle
späteren erneut. Eine Stufe, die scheitert, schreibt keine Marke. `build.sh --fresh` übergeht die Marken.

**Fortschritt.** Der Runner schreibt eine Zeile, wenn eine Stufe beginnt, endet, fortgesetzt wird oder scheitert:
`=== Stage 3/4: convert ===`, `=== Stage 3/4: convert done in 41 s ===`, `=== Stage 1/4: source resumed ===`,
`=== Stage 3/4: convert failed (exit 1) ===`. Die Build-Engine liest diese Zeilen aus dem Container-Log: Build-Status,
Builder und `llm-cli build status` zeigen die Stufe („Running modules: convert (3/4)“) und ihren Prozentwert. Eine
gescheiterte Stufe beendet den Build mit ihrem Exit-Code.

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
| `MODEL_TASK`, `OPTIMIZATION_LEVEL`, `BUILD_ID` | Aufgabe (Vorgabe `LLM`), Optimierungsstufe (`fast`, `balanced`, `size`, `speed`, `aggressive`; die Rockchip-Skripte werten sie nicht aus), Build-ID |
| `MAX_CONTEXT_LENGTH` | nur, wenn für den Build gesetzt |
| `MODEL_REVISION` | der Hugging-Face-Commit des bereitgestellten Modells (nicht bei einem lokalen Modell); Teil der Prüfsumme fürs Fortsetzen |
| `USE_IMATRIX`, `IMATRIX_PATH` | Build-Stufe: `1` und der Pfad von `imatrix.dat`, wenn eine Importance-Matrix berechnet wurde |
| `DATASET_PATH` | Kalibrierdatensatz, wenn der Build einen nutzt |
| `BUILD_SCRATCH_DIR`, `TMPDIR`, `HOME`, `XDG_CACHE_HOME` | beschreibbarer Arbeitsordner `/build-cache/work` und seine Unterordner |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE` | `1` in der Sandbox: Bibliotheken versuchen nie das Netzwerk |
| `PUID`, `PGID` | die IDs des Host-Benutzers (nicht unter Windows) |

Was der Stage-Runner jeder Stufe mitgibt:

| Name | Bedeutung |
|---|---|
| `STAGE_DIR` | der Stufenordner `$BUILD_SCRATCH_DIR/stages/<Auftrag>/`: die Marken, der Build-Plan `build_config.sh` und was eine Stufe der nächsten übergibt (`quantize_jobs.sh`, `sdk_environment.sh`) |
| `MODULES_DIR` | der Ordner der Stufenmodule (`/app/modules` im Image) |
| `STAGE_RUNNER`, `STAGE_NAME` | gesetzt, solange der Runner eine Stufe ausführt; ein Modul, das ohne `STAGE_RUNNER` startet, übergibt an den Runner |
| `PACKAGE_REQUESTED` | `1`, wenn `build.sh --package` die Stufe target um das Paket für das Board gebeten hat (auch ohne Hardware-Profil), sonst `0`; eine Eingabe nur der Stufe target |
| `stage_output DATEI` | eine Shell-Funktion: nennt eine Datei, die die Stufe geschrieben hat und die das Fortsetzen prüft |

Einbindungen:

| Pfad im Container | Modus | Inhalt |
|---|---|---|
| `/build-cache/output` | lesen und schreiben (Build-Stufe) | wohin die Stufen ihre Ergebnisse schreiben |
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
  Quantisierung und den llama.cpp-Tag, in seiner Stufe target, und die Statistik der Stufe convert als
  `conversion`); die Engine ergänzt Dateiliste, Hashes und Build-Daten.
- Im Auftrag `imatrix` schreibt die Stufe convert `$BUILD_CACHE_DIR/imatrix/imatrix.dat`.
- Nenne jede Datei, die eine spätere Stufe braucht, und jedes Ergebnis mit `stage_output DATEI`: Ein Fortsetzen führt
  eine Stufe erneut aus, wenn eine davon fehlt.
- Beende das Modul bei jedem Fehler mit einem Exit-Code ungleich 0 und schreib den Grund nach stderr. Der Runner
  beendet den Build mit diesem Code, und die letzten Log-Zeilen gehen an den Benutzer und an das Self-Healing;
  formuliere sie also klar (`die "RKLLM needs an RK3588 or RK3576 NPU; ..."`).

### 6.3 Regeln

- Beginne jedes Modul mit `set -euo pipefail` und lege seine Arbeit in `main`, das das Modul nur aufruft, wenn es
  selbst gestartet wurde (`if [[ "${BASH_SOURCE[0]}" == "${0}" ]]`): Dann kann eine spätere Stufe es für seine
  Funktionen einlesen.
- Lass `build.sh` so, wie die Vorlage es hat; leg die Arbeit in die Stufe, zu der sie gehört, und halte den Kopf
  jedes Moduls (Image, Zielsystem, was die Stufe tut) wahr.
- In der Stufe config: Schreib Format und Board klein, vereinheitliche die Quantisierung und nimm genau die Aliasse
  an, die du in den Capabilities angegeben hast. Lehne nicht unterstützte Werte mit einer Meldung ab, die das
  Unterstützte nennt, bevor etwas Langsames läuft. Nutze dasselbe Standard-Board wie `default_board`.
- Gib Werte über den Build-Plan an die späteren Stufen weiter (`printf '%s=%q\n'`, wie `config_module.sh` es tut),
  nie als ungequoteten Text.
- Schreib große Zwischendateien nach `$BUILD_SCRATCH_DIR`, nie ins Image oder an einen festen Pfad unter `/tmp`.
- Lade nie etwas herunter; die Stufen haben kein Netzwerk.
- Führe in einer Stufe nie einen Benchmark aus: Ein Modell für ein Board wird auf dem Board gemessen.
- Halte Capabilities und Skripte mit Tests im Einklang, so wie `tests/test_foundation.py`,
  `tests/test_review_capabilities_paths.py` und `tests/test_build_stages.py` es für Rockchip tun.

### 6.4 Beispiel

Die Stufen convert und target eines minimalen GGUF-Targets für die Capabilities aus
[Abschnitt 4.2](#42-capabilities); `build.sh` und die Stufen source und config kommen unverändert aus der Vorlage
(die Stufe config schreibt `QUANT_TYPE` und die Pfade in den Build-Plan). Sie setzen voraus, dass das Image llama.cpp
in `/usr/src/llama.cpp` mit den Werkzeugen in `build/bin` enthält, wie das Rockchip-Image. Beide bestehen `bash -n`
und ShellCheck; für F16/BF16-Ausgabe, die Importance-Matrix, Aliasse und weitere Prüfungen siehe
`targets/Rockchip/modules/`.

```bash
#!/bin/bash
# convert_module.sh - stage 3 of the target "my_board": the model as an F16 GGUF for the target stage
# Image: the Dockerfile of my_board (llama.cpp in /usr/src/llama.cpp). Target: my_board (aarch64 Linux).
set -euo pipefail

main() {
    # The plan of the config stage: MODEL_SOURCE, MODEL_NAME, QUANT_TYPE, OUTPUT_DIR, SCRATCH_DIR, LLAMA_BASE, ...
    # shellcheck source=/dev/null
    source "$STAGE_DIR/build_config.sh"
    local f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf"
    python3 "$LLAMA_BASE/convert_hf_to_gguf.py" "$MODEL_SOURCE" --outfile "$f16" --outtype f16
    stage_output "$f16"   # a resume runs this stage again when the file is gone
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Started by hand: the build up to this stage, through the stage runner.
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to convert "$@"
    main "$@"
fi
```

```bash
#!/bin/bash
# target_module.sh - stage 4 of the target "my_board": quantization and build_info.json
# Image: the Dockerfile of my_board (llama-quantize in /usr/src/llama.cpp/build/bin). Target: my_board.
set -euo pipefail

main() {
    # shellcheck source=/dev/null
    source "$STAGE_DIR/build_config.sh"
    local f16="$SCRATCH_DIR/tmp-${MODEL_NAME}-F16.gguf" out="$OUTPUT_DIR/${MODEL_NAME}-${QUANT_TYPE}.gguf"
    "$LLAMA_BASE/build/bin/llama-quantize" "$f16" "$out" "$QUANT_TYPE"
    rm -f "$f16"
    printf '{"model": "%s", "quantization": "%s"}\n' "$MODEL_NAME" "$QUANT_TYPE" > "$OUTPUT_DIR/build_info.json"
    stage_output "$out"
    stage_output "$OUTPUT_DIR/build_info.json"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    [ -n "${STAGE_RUNNER:-}" ] || exec bash "$(dirname "${BASH_SOURCE[0]}")/build.sh" --to target "$@"
    main "$@"
fi
```

### 6.5 Die Prüfungen der Stufe convert

Die convert-Module von Rockchip und der Vorlage führen um jede Umwandlung herum `scripts/model_check.py` aus (mit dem
`python3` des Images und nur der Standardbibliothek; der Modulgenerator kopiert die Datei in das `scripts/` eines
neuen Targets, lass sie dort). Sie leistet, was das convert-Modul der Version 1.x im Entwurf des Maintainers
versprochen hat:

| Befehl | Wann | Was er tut |
|---|---|---|
| `input PFAD --format FORMAT --stats DATEI` | vor dem Umwandeln | Erkennt die Eingabe: einen Hugging-Face-Ordner mit SafeTensors- oder PyTorch-Gewichten, eine GGUF-Datei (nichts umzuwandeln), ein ONNX-Modell, eine einzelne Gewichtsdatei, einen Ordner mit GGUF-Dateien. Lehnt mit dem Grund ab, was das Format nicht verwenden kann (`--onnx-next TEXT` ergänzt, wohin ein ONNX-Modell auf diesem Target gehört), prüft die Integrität der Eingabe (ihre JSON-Dateien, die SafeTensors-Header gegen die Dateigrößen, die Teildateien, die ein Index nennt, PyTorch-Archive, einen GGUF-Header) und beginnt die Statistik. |
| `begin DATEI --state DATEI [--scratch ORDNER]` | bevor ein Konverter DATEI schreibt | Behält eine vorhandene DATEI als `DATEI.convert-backup`, bis die neue ihre Prüfungen bestanden hat (eine Zwischendatei `tmp-*` des Arbeitsordners wird einfach ersetzt). |
| `watch ORDNER --state DATEI` | bevor ein Format-Helfer läuft | Merkt sich die Dateien von ORDNER mit Größe und Zeit, damit `output --watched` das Modell findet, das der Helfer geschrieben hat (eine neue Datei oder eine, die er überschrieben hat), und ein Fehler es entfernt. |
| `output DATEI --format gguf --type TYP --seconds N --stats DATEI --tools ORDNER` | nach dem Konverter | Prüft das GGUF (die Daten jedes Tensors liegen in der Datei), führt den Funktionstest mit `llama-completion` (oder `llama-cli`) aus ORDNER aus: 8 Tokens, höchstens `--test-seconds` (120), offline. `--test-skip GRUND` überspringt den Test mit einem Hinweis. Vervollständigt die Statistik. |
| `test DATEI --tools ORDNER --result DATEI` | in der Stufe target, bevor das Paket bleibt | Derselbe Funktionstest eines anderen GGUF (des quantisierten Modells; ein Embedding-Modell läuft nicht): `--what TEXT` nennt es bei einem Fehler, `--stage NAME` seine Logzeilen. Schreibt das Ergebnis, das Werkzeug und die Zeile des Manifests nach `--result`. |
| `keep --state DATEI` | wenn die Stufe gelungen ist | Entfernt die Sicherungen. |
| `restore --state DATEI --failed` oder `--stopped` | wenn die Stufe scheitert; bei ihrem nächsten Start | Entfernt, was der Lauf geschrieben hat, und legt die Sicherungen zurück (`--stopped`: was ein abgebrochener Lauf hinterlassen hat). Eine Datei, deren Sicherung nicht mehr da ist, bleibt, wie sie ist: Die frühere Datei wurde von Hand zurückgelegt. |

Die Statistik (`convert_stats.json` im Stufenordner) kommt als `conversion` in `build_info.json`: `input_format`,
`weights`, `input_bytes` (was der Konverter liest: Eine Gewichtsdatei, die er beiseitelässt, etwa Mistrals
`consolidated.safetensors` neben den Teildateien `model*.safetensors`, zählt nicht), `converted`, `output_format`,
`output_type`, `output_bytes`, `ratio` (Eingabe / Ausgabe),
`seconds`, `functional_test` (`passed`, `skipped`, `timed out` oder `stopped`) und `functional_test_tool`; nur Zahlen
und Wörter, keine Pfade. Ein Funktionstest, der mit einem Fehler endet oder abstürzt (ein Signal wie der Abbruch von
llama.cpp bei einem Tensor, den es nicht verwenden kann), lässt die Stufe mit den letzten Zeilen des Werkzeugs
scheitern: Das GGUF funktioniert nicht. Eine Zeitüberschreitung oder ein von außen angehaltener Lauf (SIGKILL des
Out-of-Memory-Killers, SIGTERM) ist ein Hinweis. Übersprungen wird er mit einem Hinweis, wenn das Image keines der beiden Werkzeuge hat oder es dort nicht startet, bei einem Embedding-Modell, bei den NPU-Formaten (das Modell läuft auf dem Board) und im
Auftrag `imatrix` (dort führt `llama-imatrix` das Modell aus).

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

1. **Ist das Target geladen und sind seine Capabilities gültig?** `llm-cli mojo` meldet den Code
   `targets.<ID>.capabilities`, wenn der Block fehlt oder ungültig ist; der Fehler selbst steht im Log. So siehst du
   den Schemafehler direkt:

   ```bash
   python -c "import yaml; from orchestrator.schemas.targets import TargetCapabilities as T; \
   T.model_validate(yaml.safe_load(open('targets/my_board/target.yml', encoding='utf-8'))['capabilities']); print('ok')"
   ```

   `llm-cli targets validate <ID>` macht dieselbe Prüfung über das Framework und gibt jeden Schemafehler aus
   (Exit-Code 1, wenn der Block ungültig ist).
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
| Runtime-Block | Hat `target.yml` einen Block `runtime:`, passt er zu seinem Schema: bekannte Architekturen, Backends und Programme, `built_from: source`, einfache Hinweis-Flags, keine weiteren Schlüssel ([Abschnitt 3.4](#34-was-der-generator-schreibt-und-was-du-ergänzt)). |
| Festgelegte Images | In jeder Datei, deren Name in beliebiger Schreibweise mit `dockerfile` beginnt (`Dockerfile`, `dockerfile`, `Dockerfile.gpu`), muss jedes Image, das `FROM`, `COPY --from=` oder `RUN --mount=...,from=` verwendet, per Digest festgelegt sein (`name@sha256:` und 64 Hex-Ziffern). Ausgenommen sind offizielle Basis-Images mit Tag (Namen, die mit `debian:`, `ubuntu:`, `rocm/dev-ubuntu-`, `rocm/rocm-terminal:` oder `python:` beginnen), eine frühere Build-Stufe (`FROM ... AS builder`, danach `COPY --from=builder`) und eine Stufennummer (`--from=0`). Die `nvidia/cuda`-Images von NVIDIA brauchen einen Digest. Kommentarzeilen werden übersprungen. Eine Variable wie `FROM ${BASE_IMAGE}` wird abgelehnt, weil sie keine Image-Angabe ist. |
| Kein vorgebautes llama.cpp | Das Framework teilt Rezepte, keine Binärdateien ([Abschnitt 5.1](#51-llamacpp-im-image-rezepte-keine-binärdateien)). In Dockerfiles: kein Image, dessen Name `llama.cpp`, `llama-cpp`, `llamacpp` oder `llama-server` enthält, und keines der früheren Laufzeit-Images des Frameworks (`ghcr.io/smilez1985/llm-runtime`, `ghcr.io/llm-framework/builder`, `ghcr.io/llm-framework/inference-node`), auch nicht per Digest festgelegt; kein `COPY --from=` (oder `RUN --mount=...,from=`) von llama.cpp-Programmen aus einem anderen Image, auch nicht über eine Build-Stufe, die nur dieses Image nennt (`FROM acme/tools@sha256:... AS tools`, dann `COPY --from=tools /usr/bin/llama-server ...`). Eine Stufe, deren `RUN`-Schritt mit CMake kompiliert oder die die Programme aus einer solchen Stufe kopiert hat, baut sie aus dem Quelltext. In Dockerfiles, Skripten (`.sh`, `.py`, `.ps1`, `.bat`, `.cmd` und jede Datei, deren erste Zeile mit `#!` beginnt) und YAML-Dateien: kein Download eines llama.cpp-Releases (`.../llama.cpp/releases/download/...`, Namen von Release-Archiven wie `llama-b11429-bin-...`), kein Download eines llama.cpp-Programms (`curl`, `wget`, `ADD` und Ähnliches mit `llama-server`, `llama-quantize`, `libllama` und den anderen Werkzeugnamen im selben Befehl; eine Pipeline wie `curl ... \| tar -x ... llama-server` ist ein Befehl), keine Paketquelle mit vorgebauten `llama-cpp-python`-Wheels und keine Installation eines llama.cpp-Pakets (`apt-get install llama.cpp`, `apk add`, `conda install` und andere). Kommentarzeilen werden übersprungen; der Wissens-Schnappschuss wird nicht geprüft. |
| Shell-Sperrliste | Jede Zeile jeder `*.sh`-Datei, Kommentare eingeschlossen, außer die gleiche Zeile steht in der Datei der Vorlage mit demselben Pfad (für `modules/build.sh`: `targets/_template/modules/build.sh`). Abgelehnt werden: Downloads (`curl`, `wget`, `aria2c` und `fetch` als Wort, also auch `git fetch`), das Löschen von `/` (`rm -rf /`), `chmod 777`, `sudo`, `mkfs` und `dd if=`, der Docker-Socket oder `docker run`, `eval`, `source` oder `.` mit dem Ergebnis von `$(...)`, `sh -c "$(...)"` und Prozess-Substitution `<(...)`, außer als reine Eingabe (`done < <(grep ...)`). Downloads gehören festgelegt ins Dockerfile. |
| Keine Download-Option von llama.cpp | Dieselben Skriptzeilen (jede Zeile, die nicht die der Vorlage ist): keine CMake-Option `-DNAME=WERT`, mit der llama.cpp beim Bauen etwas herunterladen oder `llama-server` Unterprozesse bekommen würde, nach der Regel des Build-Rezepts (`refused_option`): eine Option aus `NEVER_SET` mit beliebigem Wert, eine aus `DOWNLOAD_OPTIONS_OFF` oder `LLAMA_CURL` mit einem anderen Wert als `OFF`, `FETCHCONTENT_FULLY_DISCONNECTED` anders als `ON`, `LLAMA_SUBPROCESS` anders als `OFF`. Jede Schreibweise zählt, die die Shell cmake als solche Option übergibt: `-DNAME:TYP=WERT`, `-D NAME=WERT` (cmake liest `-D` und das nächste Wort als eine Option) und Anführungszeichen um das Wort, den Namen oder den Wert. Das deckt die CMake-Flags eines Moduls ab (`CMAKE_OPTIONS` in `modules/config_module.sh`) und jeden `cmake`-Aufruf seiner Skripte. |
| Shell-Syntax | Jede `*.sh`-Datei besteht `bash -n` (entfällt, wo bash nicht installiert ist). |
| Nur Text | Keine Datei enthält ein NUL-Byte: Binärdateien werden abgelehnt. Beim Export muss außerdem jede Datei UTF-8-Text sein. |
| Platzhalter | Keine Datei außer dem Wissens-Schnappschuss `knowledge/initial_knowledge.json` (gecrawlte Seiten) und seinen Lizenzdateien `knowledge/NOTICE` und `knowledge/LICENSES/` enthält einen Platzhalter der Vorlage (`[MODULE_NAME]`, `[Hardware-Familie]`, `[IHRE_ARCHITEKTUR]`, `[Hersteller]`, `[QUANTIZATION_LOGIC]`, `[CPU_FLAGS]`, `[CMAKE_FLAGS]`, `[SDK_SETUP_COMMANDS]`, `[PACKAGING_COMMANDS]`, `[ihr-target-name]`, `{packages_str}` oder `YYYY-MM-DD`), und kein Wert in `target.yml` enthält Text in eckigen Klammern wie `[Board Name 1]`. |

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

## 10. Was die Stufenmodule aus Version 1.x behalten haben

Version 1.x baute ein Target schon mit diesen vier Skripten, gesteuert von der Hardware-Probe; eine Zeit lang rief
der Build nur einen einzelnen Verteiler `build.sh` auf, und die Skripte lagen beiseite. Seit der Entscheidung U9 des
Maintainers sind sie wieder der Build, und die Arbeit des Verteilers liegt in der Stufe, zu der sie gehört. Ihre
Funktionen aus 1.x sind geblieben:

- **Hardware-Profil.** Mit einem Profil des Boards (`build.sh --hardware-config DATEI` oder das vierte Argument des
  Entrypoint-Modus `pipeline`) erzeugt die Stufe config daraus die CMake-Toolchain und die Compiler-Flags für dieses
  Board (die der Vorlage mit den CPU- und CMake-Flags des Wizards), und die Stufe target eines GGUF-Builds
  cross-kompiliert außerdem `llama-cli` und `llama-server` aus den llama.cpp-Quellen des Images und packt sie mit dem
  Modell, einer Model Card und Hilfsskripten nach `output/packages/`. Das Profil ist die Datei, die die Hardware-Probe
  auf dem Board schreibt ([Abschnitt 11](#11-die-datei-der-hardware-probe)): Die Stufe config von Rockchip nimmt das
  System aus `ARCH` und die Kerne aus `CPU_MODEL` (Cortex-A55, A72 und A76 bekommen ihre CPU-Flags,
  `SUPPORTS_NEON`, `SUPPORTS_AVX2` und `SUPPORTS_AVX512` ihre GGML-Flags) und lehnt ein System ab, für das ihr Image
  keinen Compiler hat (nur aarch64 und x86_64). Ein x86_64-Profil bekommt `-march=native` nur, wenn es das Profil
  dieses Build-Rechners selbst ist (dasselbe CPU-Modell und dieselben CPU-Flags wie `/proc/cpuinfo` hier); ein Profil
  eines anderen PCs bekommt die CPU-Merkmale seiner Probe, so wie das Build-Rezept einen Build für einen anderen
  Rechner plant (alle 14 x86-Optionen von GGML ausdrücklich, ihre Compiler-Flags wie `-mavx2` auf `-march=x86-64`,
  ein Merkmal, das die Probe nicht bestätigt hat, weggelassen, und bei einer Probe-Datei aus 1.x die Merkmale, die ihr
  `CPU_FLAGS_ALL` nennt), damit die Programme auf diesem PC laufen und nicht nur auf einer CPU wie der des
  Build-Rechners. Ein 32-Bit-ARM-System (`ARCH=armv7` und ähnliche) wird mit dem
  schlichten Grund abgelehnt: Die Rockchip-Boards haben 64-Bit-CPUs, und das Image hat nur die aarch64-Cross-Compiler;
  installiere also das 64-Bit-System des Boards und lass die Probe erneut laufen. Die Build-Jobs werden auf die Kerne
  des Boards begrenzt (`CPU_CORES` oder `CPU_CORES_LOGICAL` der Probe-Datei). Ein Build des Frameworks hat kein
  Profil: Die Runtime des Boards wird aus der eigenen Probe des Boards gebaut (`llm-cli device runtime build`), und
  das Board-Paket kommt von `llm-cli deploy package` oder `llm-cli device runtime deploy`.
- **Der Plan wird geprüft, bevor er bleibt.** Die Stufe config sucht die geplanten Compiler im Image (fehlt einer,
  plant sie nichts), baut und linkt ein kleines C- und C++-Programm mit den geplanten Flags (und führt sie aus, wenn
  sie für diesen Rechner sind), prüft am ELF-Kopf, dass sie für das System des Boards sind, lässt `cmake -P` die
  erzeugte Toolchain-Datei lesen und prüft den Plan `build_config.sh` in einer sauberen Shell: Er muss ein gültiges
  Skript sein, das jeden Namen setzt, den die späteren Stufen lesen. Ein Plan, der eine Prüfung nicht besteht, wird
  nicht geschrieben.
- **Das Paket für das Board.** `output/packages/<Modell>_<Typ>_<Architektur>_<Zeit>/` enthält das GGUF, `llama-cli`
  und `llama-server`, die Toolchain-Datei (`cmake_toolchain.cmake`) und das Hardware-Profil, eine Model Card
  (`README.md`), `test_model.sh` (prüft die Dateien gegen `SHA256SUMS` und bittet das Modell dann mit `llama-cli` um
  eine kurze Antwort), `deploy.sh` (`./deploy.sh USER@HOST [ZIEL]` kopiert das Paket über `ssh` und `scp` mit
  `StrictHostKeyChecking=yes`, der Host-Key des Boards muss also schon bekannt sein, nach ZIEL, Vorgabe
  `llm/<Paket>` im Home-Ordner des Benutzers auf dem Board, und prüft die Kopie dort; `./deploy.sh [ZIEL]` auf dem
  Board selbst kopiert es nach ZIEL, Vorgabe `/opt/llm`), `MANIFEST.json` mit
  jedem Detail des Builds (Modell, Quantisierung, llama.cpp, Board, Profil, Flags, Tests, jede Datei mit Größe und
  SHA-256) und `SHA256SUMS`. Die Helfer kommen aus `scripts/package_test_model.sh`, `scripts/package_deploy.sh` und
  `scripts/board_package.py` des Targets. Bevor das Paket bleibt, läuft das Modell einmal auf diesem Rechner
  (`llama-completion` des Images, wenn es eins hat), mit dem Funktionstest der Stufe convert
  (`scripts/model_check.py test`, [Abschnitt 6.5](#65-die-prüfungen-der-stufe-convert)): Ein Lauf, der mit einem
  Fehler endet oder abstürzt, stoppt das Paket, und was nichts über das Modell sagt (eine Zeitüberschreitung, ein
  Stopp von außen, ein Werkzeug, das dieser Rechner nicht ausführen oder starten kann, ein Embedding-Modell), ist ein
  Hinweis im Manifest. Die Programme müssen ELF-Programme für das Board sein, und jede Datei muss zu `SHA256SUMS`
  passen. Ihr Cross-Build schaltet jede Option von llama.cpp ab, die etwas herunterladen kann, und baut `llama-server`
  ohne Unterprozesse, nach den CMake-Flags des Moduls, damit diese sie nicht wieder einschalten. Was keine spätere
  Option abschaltet, stoppt das Paket vor allem Langsamen, so wie das Dockerfile des Images solche `LLAMA_CMAKE_ARGS`
  ablehnt: ein Flag des Moduls, das eine Option aus `NEVER_SET` des Rezepts setzt (auch als `-D NAME=WERT` oder
  `-DNAME:TYP=WERT`) oder CMake ein Skript ausführen lässt (`-C`, `-P`). Das Paket kopiert das
  Modell neben das des Builds, deshalb kommt zuerst die Speicherplatz-Regel des Frameworks
  (`scripts/board_package.py room`, die Reserve von `orchestrator/Core/disk_plan.py`: Die Platte von
  `output/packages/` muss fassen, was das Paket kopiert, und 2 GiB oder 2 % von ihr frei lassen): für das Modell vor
  dem Funktionstest und dem Cross-Build und für jede Datei direkt vor dem Kopieren; eine Platte, die es nicht fasst,
  stoppt die Stufe mit den Worten der Regel und ohne Paket. Der Build-Container sieht die Reservierungen des
  Benutzerordners nicht. `build.sh --package` (auch ein von Hand gestartetes `target_module.sh` und die
  Entrypoint-Modi `target` und `pipeline`) macht das Paket auch ohne Profil, mit den schlichten Vorgaben des Targets
  (aarch64 bei Rockchip), und das Manifest sagt es (`hardware.source: defaults`). Das eigene Deployment des
  Frameworks bleibt `llm-cli deploy` und der Tab **Deployment**, über seine SSH-Sitzung mit dem Schlüssel und den Known
  Hosts des Frameworks.
- **llama.cpp klonen.** `source_module.sh --clone-llama-cpp` (oder der Entrypoint-Modus `source
  --clone-llama-cpp`), von Hand in einem Container mit Netzwerk gestartet, klont llama.cpp bei `LLAMA_CPP_COMMIT`
  (eine vollständige Commit-ID; ohne sie der Pin des Frameworks, `LLAMA_CPP_PIN_COMMIT` im Modul) aus
  `LLAMA_CPP_REPO_OVERRIDE` (eine https-Adresse ohne Benutzernamen, Passwort, Token oder Query; ohne sie Upstream),
  prüft, dass der Checkout dieser Commit ist, und verlinkt es als `LLAMA_CPP_PATH`, für ein Image ohne llama.cpp. Ein
  Build tut das nie: Sein Image bringt das gepinnte llama.cpp mit, beim Bau des Images durch das Framework aus der
  Repository-Überschreibung geholt.
- **Die Moduloptionen.** `--input`, `--output`, `--model-name`, `--quantization` und `--hardware-config`
  funktionieren bei `build.sh`, bei jedem von Hand gestarteten Stufenmodul und bei jedem Stufenmodus des
  Entrypoints, wie im Entwurf des Maintainers für 1.x: `convert_module.sh --input ORDNER --output DATEI --model-name
  NAME` schreibt das F16-GGUF nach DATEI (es bleibt); `target_module.sh --input DATEI.gguf --quantization TYP
  --model-name NAME` quantisiert ein GGUF, ohne es erneut zu konvertieren, und behält es (ohne `--quantization`
  Q4_K_M, ohne `--model-name` der Dateiname ohne `.gguf` und `.fp16`); `config_module.sh --hardware-config DATEI`
  braucht kein Modell und schreibt Toolchain und Plan in den Stufenordner. Ein Pfad als Option wird vom aktuellen
  Ordner aus absolut gemacht.

Ein Target, das vor dieser Änderung erzeugt wurde, behält sein eigenes `build.sh`, das weiter wie bisher läuft.

## 11. Die Datei der Hardware-Probe

Die Probe schreibt `target_hardware_config.txt` auf dem Rechner, der die Programme ausführen wird (ein Board oder ein
PC). Ein einziger Parser liest sie, `orchestrator/runtime/profile.py`, und die vollständige Liste der Schlüssel mit
Quelle, Normalisierung und Prüfung jedes Schlüssels ist
[`configs/hardware_probe_schema.yml`](../configs/hardware_probe_schema.yml). Dieser Abschnitt richtet sich an alle,
die eine Probe schreiben, ein Profil lesen oder eines von einem Rechner ohne Netz mitbringen.

Drei Probes schreiben sie mit denselben Schlüsseln und Regeln: `scripts/hardware_probe.py` (der Hauptweg; nur
Standardbibliothek, Python 3.8 oder neuer) und die Ersatzwege `scripts/hardware_probe.sh` (jede POSIX-Shell unter
Linux) und `scripts/hardware_probe.ps1` (Windows). Jede trägt eine Kopie der Prüfungen und Tabellen des Schemas, und
`tests/test_hardware_probe.py` vergleicht die Kopien mit dem Schema und führt die `.py` und die `.sh` auf denselben
Testrechnern aus. Dateien der älteren Skripte (Schema 1) werden umgewandelt
([Abschnitt 11.3](#113-wie-das-framework-ein-profil-prüft)).

### 11.1 Format (Schema 2)

- UTF-8 (eine Byte-Order-Mark wird akzeptiert), LF-Zeilenenden (CRLF wird akzeptiert); Zeilen, die mit `#` beginnen,
  sind Kommentare.
- Die erste Zeile, die kein Kommentar ist, lautet `PROBE_SCHEMA=2`.
- Ein `KEY=VALUE` je Zeile. Schlüssel sind großgeschrieben (`A-Z`, Ziffern, `_`) und kommen höchstens einmal vor.
- Werte stehen in einer Zeile, ohne Anführungszeichen, mit höchstens 4096 Zeichen; Listen sind durch Kommas ohne
  Leerzeichen getrennt.
- Jeder `ON`/`OFF`-Schlüssel wird immer geschrieben: `ON`, `OFF` oder `UNKNOWN`. `UNKNOWN` heißt „diese Methode kann
  es nicht feststellen“ (etwa AVX-VNNI unter Windows); es wird nie aus einem Modellnamen geraten.
- Jede andere Angabe, die die Probe nicht ermitteln kann, wird weggelassen, nie leer geschrieben, und ein Code kommt
  in `PROBE_WARNINGS` (etwa `not_determined:LIBC_VERSION` oder `needs_root:RKNPU_DRIVER_VERSION`).
- Schlüssel je GPU sind nummeriert: `GPU0_VENDOR`, `GPU1_VENDOR`, ... unterhalb von `GPU_COUNT`.
- Die JSON-Ansicht hat dieselben Schlüssel mit denselben Zeichenketten als Werten, `PROBE_SCHEMA` eingeschlossen.

Beispiel: ein RK3566-Board mit DietPi, gekürzt (die vollständige Datei ist
`tests/fixtures/hardware_probe/schema2_rk3566_dietpi.txt`):

```text
PROBE_SCHEMA=2
PROBE_TOOL=sh
PROBE_TOOL_VERSION=3.0.0
GENERATED_AT=2026-10-11T08:15:00Z
OS=linux
OS_ID=debian
OS_FAMILY=debian
OS_VARIANT_ID=dietpi
OS_VERSION_ID=12
KERNEL=6.1.99-vendor-rk35xx
ARCH=arm64
LIBC=glibc
LIBC_VERSION=2.36
INIT_SYSTEM=systemd
CPU_MODEL=Cortex-A55
CPU_CLUSTERS=0-3:0x41:0xd05:1800
SUPPORTS_AVX2=OFF
SUPPORTS_NEON=ON
SUPPORTS_DOTPROD=ON
SUPPORTS_FP16_ARITH=ON
SUPPORTS_I8MM=OFF
RAM_TOTAL_MB=3912
SOC_VENDOR=rockchip
SOC_MODEL=rk3566
NPU_DEVICE=/dev/rknpu
SUPPORTS_RKNN=ON
GPU_COUNT=1
GPU0_VENDOR=arm
GPU0_DISCRETE=OFF
GPU0_UNIFIED=ON
PROBE_WARNINGS=needs_root:RKNPU_DRIVER_VERSION
```

### 11.2 Schlüssel

| Gruppe | Schlüssel |
|---|---|
| Meta | `PROBE_SCHEMA`, `PROBE_TOOL` (`python`, `sh`, `ps1`), `PROBE_TOOL_VERSION`, `GENERATED_AT` (UTC), `PROBE_WARNINGS` |
| Betriebssystem | `OS` (`linux`, `windows`, `macos`, `freebsd`, `android`), `OS_ID`, `OS_ID_LIKE`, `OS_FAMILY` (`debian`, `rhel`, `alpine`, ...), `OS_VARIANT_ID` (`dietpi`, `armbian`, ...), `OS_VERSION_ID`, `OS_VERSION_CODENAME`, `OS_PRETTY_NAME`, `KERNEL`, `KERNEL_ARCH`, `INIT_SYSTEM` (`systemd`, `openrc`, `sysvinit`, ...) |
| Binärkompatibilität | `ARCH` (`x86_64`, `arm64`, `armv7`, ...; die Architektur des Userlands, ein 32-Bit-System auf einem 64-Bit-Kernel ist also `armv7`), `LIBC` (`glibc`, `musl`, `ucrt`, ...), `LIBC_VERSION` |
| CPU | `CPU_MODEL`, `CPU_VENDOR`, `CPU_CORES_LOGICAL`, `CPU_CORES_PHYSICAL`, `CPU_CLUSTERS` (nur ARM: CPUs, Implementer, Part und höchste MHz je Cluster), `CPU_FLAGS_ALL`, `CPU_FLAGS_SOURCE` |
| x86-Merkmale | die 14 x86-Optionen von ggml: `SUPPORTS_SSE42`, `SUPPORTS_AVX`, `SUPPORTS_AVX2`, `SUPPORTS_BMI2`, `SUPPORTS_F16C`, `SUPPORTS_FMA`, `SUPPORTS_AVX512` (AVX-512 F), `SUPPORTS_AVX512_VBMI`, `SUPPORTS_AVX512_VNNI`, `SUPPORTS_AVX512_BF16`, `SUPPORTS_AVX_VNNI`, `SUPPORTS_AMX_TILE`, `SUPPORTS_AMX_INT8`, `SUPPORTS_AMX_BF16` |
| ARM-Merkmale | `SUPPORTS_NEON`, `SUPPORTS_DOTPROD`, `SUPPORTS_FP16_ARITH`, `SUPPORTS_I8MM`, `SUPPORTS_SVE`, `SUPPORTS_SVE2`, `SUPPORTS_SME`, `SUPPORTS_BF16_ARM` |
| Speicher, Platte | `RAM_TOTAL_MB` (nutzbarer Speicher, nie die Summe der Speicherplätze), `SWAP_TOTAL_MB`, `RAM_AVAILABLE_MB`, `DISK_FREE_MB`, `RAM_TYPE`, `RAM_SPEED_MTS` |
| SoC, NPU | `SOC_VENDOR`, `SOC_MODEL` (`rk3566`, ...), `BOARD_MODEL`, `NPU_VENDOR`, `NPU_MODEL`, `NPU_DEVICE`, `RKNPU_DRIVER_VERSION`, `RKLLM_RUNTIME_VERSION`, `RKNN_RUNTIME_VERSION`, `USB_ROCKCHIP_PID`, `USB_ROCKCHIP_MODE` (ein per USB angeschlossenes Board), `SUPPORTS_RKNN`, `SUPPORTS_HAILO`, `SUPPORTS_METIS`, `SUPPORTS_MX3`, `SUPPORTS_CORAL`, `SUPPORTS_INTEL_NPU` |
| GPU | `GPU_COUNT`; je GPU `_VENDOR`, `_NAME`, `_PCI_ID`, `_DISCRETE`, `_UNIFIED`, `_VRAM_MB`, `_VRAM_FREE_MB`, `_DRIVER_VERSION`, `_COMPUTE_CAP` (NVIDIA), `_GFX_TARGET` (AMD), `_SOURCE`; `NVIDIA_DRIVER_CUDA_MAX`, `SUPPORTS_CUDA`, `SUPPORTS_INTEL_XPU`, `VULKAN`, `VULKAN_DEVICES`, `HIP_RUNTIME`, `ROCM_VERSION`, `METAL` |
| Werkzeuge | `TOOL_CC`, `TOOL_CXX` (`gcc-12.2.0`, `clang-18.1.3`), `TOOL_CLANG`, `TOOL_MSVC`, `TOOL_CMAKE`, `TOOL_NINJA`, `TOOL_MAKE`, `TOOL_GIT`, `TOOL_CCACHE`, `TOOL_NVCC`, `TOOL_GLSLC`, `TOOL_HIPCONFIG`, `TOOL_DOCKER`, `TOOL_PYTHON`: nur Versionen, nie Pfade |

Nicht in einem Profil: der Hostname, Serien- oder Teilenummern (auch nicht die RAM-Teilenummern je Steckplatz der
heutigen Windows-Probe), Netz- oder Cloud-Daten. Abgeleitete Werte wie CMake-Optionen stehen auch nicht in der Probe:
Das Framework berechnet sie aus dem Profil, sodass ein Profil von einem Rechner ohne Netz gültig bleibt.

### 11.3 Wie das Framework ein Profil prüft

Profile kommen von Geräten oder werden von Hand mitgebracht, darum behandelt der Parser sie als nicht
vertrauenswürdig:

- Eine Datei in Schema 2 wird als Ganzes abgelehnt, wenn ein Schlüssel doppelt vorkommt, ein Wert leer ist, länger als
  4096 Zeichen ist, ein Steuerzeichen enthält oder nicht zur Prüfung des Schlüssels passt (eine feste Liste, ein
  Zahlenbereich oder ein Muster), oder wenn ein Pflichtschlüssel oder ein `ON`/`OFF`-Schlüssel fehlt.
- Werte müssen zusammenpassen: Ein x86-Merkmal kann auf `arm64` nicht `ON` sein (ein ARM-Merkmal nicht auf `x86_64`),
  `CPU_CLUSTERS` gibt es nur auf ARM, `GPU<n>_COMPUTE_CAP` nur bei NVIDIA- und `GPU<n>_GFX_TARGET` nur bei AMD-GPUs,
  `LIBC=ucrt` nur unter Windows, `METAL=ON` nur unter macOS.
- `NPU_DEVICE` nennt nur einen Beschleuniger-Knoten (`/dev/rknpu`, `/dev/dri/renderD<N>` oder `/dev/dri/card<N>`,
  `/dev/accel/accel<N>`, `/dev/hailo<N>`, `/dev/apex_<N>`), nie eine Platte, den Speicher oder ein anderes Gerät,
  weil ein Deploy-Schritt den Wert an `docker run --device` übergeben kann.
- Ein Schlüssel, den das Schema nicht kennt, wird verworfen; sein Wert wird nie behalten.
- Freier Text (`CPU_MODEL`, GPU-Namen, `BOARD_MODEL`) dient nur der Anzeige und erreicht nie eine Befehlszeile oder
  einen Dateinamen.

Dateien in **Schema 1** (die Ausgabe von `hardware_probe.sh` 2.4 und `.ps1` 2.5, die das Framework bis einschließlich
Release 3.0.0 mitbrachte, ohne `PROBE_SCHEMA`-Zeile) werden weiter gelesen:

- Die bekannten Schlüssel werden umgewandelt: `CPU_ARCH_TYPE` zu `ARCH`, `TRUE`/`FALSE` zu `ON`/`OFF`, `RAM_SWAP_MB`
  zu `SWAP_TOTAL_MB`, die erste GPU zu `GPU0_*`, `DOCKER_VERSION` zu `TOOL_DOCKER`. Die CPU-Merkmale einer `.sh`-Datei
  werden aus ihrer Flag-Liste abgeleitet, nicht aus ihren eigenen `SUPPORTS_*`-Zeilen übernommen; ohne Flag-Liste
  sind sie `UNKNOWN`.
- Jedes CPU-Merkmal einer `.ps1`-Datei wird `UNKNOWN`, weil dieses Skript Windows mit falschen Konstanten fragte. Ihr
  `RAM_TOTAL_MB` (die Summe der Speicherplätze) wird verworfen.
- Der Hostname, die RAM-Teilenummern und jeder andere Schlüssel ohne Bedeutung in Schema 2 werden verworfen, ohne den
  Wert zu behalten. Ein Schlüssel, der zweimal mit verschiedenen Werten vorkommt, wird verworfen, ebenso ein Wert, der
  die Prüfung nicht besteht.
- `ON`/`OFF`-Schlüssel, die die Datei nicht hat, werden `UNKNOWN` (`OFF`, wo es sie nicht geben kann, etwa NEON auf
  x86).

Das Ergebnis trägt `PROBE_CONVERTED_FROM=1` und die Warnungen `converted_from_schema_1` und `reprobe_before_build`:
Führe die Probe vor einem Build erneut aus.

### 11.4 Der Leistungsschlüssel

`HardwareProfile.perf_key()` ist ein sha256 über die Werte, die Geschwindigkeit oder Programm ändern: `OS`, `ARCH`,
`LIBC`, `CPU_VENDOR`, `CPU_MODEL`, `CPU_CLUSTERS`, jeder `SUPPORTS_*`-Schlüssel, `SOC_MODEL`, `NPU_MODEL` und je GPU
Hersteller, PCI-ID, Compute Capability und gfx-Target. Er ist derselbe, egal welche Probe die Datei geschrieben hat und
in welcher Reihenfolge die GPUs stehen. Er ändert sich nicht mit flüchtigen Werten (freier RAM, freier VRAM, freier
Plattenplatz), dem Zeitpunkt der Probe, dem Kernel, Werkzeugversionen oder den Werten, die nur mit einem Benchmark
festgehalten werden (Betriebssystemversion, Version der C-Bibliothek, gesamter RAM, VRAM-Größe, Treiberversionen);
`recorded()` liefert diese.

### 11.5 Wo Profile liegen

`ProfileStore` hält Profile im Benutzerordner (`~/.llm-framework` oder `LLM_FRAMEWORK_HOME`):

- `hardware/local.txt` für diesen Rechner;
- `hardware/devices/<Geräte-ID>.txt` für andere Rechner, daneben `<Geräte-ID>.json` für die Bezeichnung und die
  SSH-Adresse.

Eine Geräte-ID sind die ersten 16 Hex-Zeichen des sha256 über den Fingerabdruck des SSH-Hostschlüssels des Geräts:
Sie bleibt gleich, wenn sich die IP-Adresse ändert, und enthält keinen Hostnamen. Ein Profil, das ohne SSH importiert
wird, bekommt eine zufällige ID. Die Dateien gehören nur dem Benutzer (Modus 0600 in Ordnern mit Modus 0700),
flüchtige Werte werden nicht gespeichert, und gespeicherte Dateien werden beim Zurücklesen wie Probe-Dateien geprüft.
`llm-cli scan` speichert das Profil dieses Rechners als `local`, und `llm-cli scan --device` speichert das Profil
eines Geräts unter seiner ID, und `llm-cli scan --import DATEI` (oder **Import from File** in der GUI und die Importe
des Assistenten) legt die Probe-Datei eines Boards unter einer zufälligen ID ab. KI-Clients und Ditto nennen ein
gespeichertes Profil über diese Probe-ID (`local` oder die Geräte-ID; Tools `get_hardware_probe` und
`probe_local_hardware`, Ressource `lcf://probes/{probe_id}`), nie über einen Pfad. Der lokale und der SSH-Scan des
Build-Reiters der GUI schreiben die Probe-Datei weiterhin wie in [Abschnitt 3.1](#31-das-board-untersuchen)
beschrieben.
