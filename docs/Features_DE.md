Version 3.0.0 plus die [unveröffentlichten Änderungen](../CHANGELOG_DE.md) · Zuletzt aktualisiert am 2026-10-11

[English](Features.md) · **Deutsch**

# Funktionen im Detail

Dieses Dokument erklärt, was das Framework im Inneren tut und warum, damit du immer weißt, was deine Werkzeuge
machen. Es beschreibt den Stand des Entwicklungszweigs: Release 3.0.0 plus die unveröffentlichten Änderungen im
[CHANGELOG](../CHANGELOG_DE.md). Geplantes steht in der [Roadmap](BLUEPRINT_DE.md#roadmap), nicht hier.

Zurück zur [README](../README_DE.md) · Alle Befehle: [CLI_Usage_DE.md](CLI_Usage_DE.md)

---

## Inhalt

- [Wie die Teile zusammenspielen](#wie-die-teile-zusammenspielen)
- [Targets und Capabilities](#targets-und-capabilities)
- [Build-Pipeline](#build-pipeline)
- [Build-Sandbox](#build-sandbox)
- [Self-Healing](#self-healing)
- [Übertragen auf das Board (Deployment)](#übertragen-auf-das-board-deployment)
- [Ditto, der Assistent](#ditto-der-assistent)
- [Dittos Tools](#dittos-tools)
- [Ditto-Evaluation](#ditto-evaluation)
- [Lokale Laufzeit und Modellstufen](#lokale-laufzeit-und-modellstufen)
- [Wissensbasis (RAG)](#wissensbasis-rag)
- [Deep Ingest](#deep-ingest)
- [MCP-Server](#mcp-server)
- [Tool-Berechtigungen und Audit-Log](#tool-berechtigungen-und-audit-log)
- [Geheimnisse und persönlicher Zustand](#geheimnisse-und-persönlicher-zustand)
- [Eingabeverlauf](#eingabeverlauf)
- [Hardware-Probe und neue Targets](#hardware-probe-und-neue-targets)
- [Gesundheitscheck (doctor)](#gesundheitscheck-doctor)
- [Community-Zoo](#community-zoo)
- [Secret-Scan für Mitwirkende](#secret-scan-für-mitwirkende)
- [Bekannte Lücken](#bekannte-lücken)

---

## Wie die Teile zusammenspielen

```
 GUI (PySide6)        CLI (llm-cli)        MCP-Server (llm-cli mcp serve)
      \                    |                    /
       +------------ Framework-Manager --------+
       |                   |                   |
  Build-Dienst        Ditto (Assistent)    Tool-Registry
  Orchestrator        LLM-Anbieterschicht  (Berechtigungen, Bestätigung, Audit-Log)
  Build-Engine ---> Docker (Konvertierungs-Container in der Sandbox)
       |                   |
  Target-Capabilities  Wissensbasis (FastEmbed + eingebettetes Qdrant)
  Self-Healing         Lokale Laufzeit (verwalteter llama-server)
  Deployment (SSH)
```

- **GUI**, **CLI** und **MCP-Server** sind drei Wege, dasselbe Framework zu bedienen.
- Builds laufen über einen einzigen **Build-Dienst**: Er prüft eine Anfrage gegen die Capabilities des Targets,
  startet sie, meldet Status und Logs, bricht sie ab und listet fertige Builds auf.
- **Ditto** und **MCP-Clients** handeln nur über die **Tool-Registry**. Sie prüft deine Berechtigungen, fragt wo
  nötig nach einer Bestätigung und schreibt jeden Aufruf ins Audit-Log.

---

## Targets und Capabilities

Ein Target ist ein Ordner in `targets/` (zum Beispiel `targets/Rockchip/`) mit einer Dockerfile, Build-Skripten und
einer `target.yml`. Ihr Block `capabilities:` legt genau fest, was das Target bauen kann:

- Boards mit CPU, NPU, RAM und Beispielgeräten sowie ein Standard-Board (Rockchip: `rk3588`, das Board, das die
  Build-Skripte ohne Angabe nehmen),
- Formate mit ihrer zulässigen Eingabe (`hf` für ein Hugging-Face-Modell, `onnx` für eine ONNX-Datei),
  Quantisierungen (insgesamt und pro Board), Standard-Quantisierungen (insgesamt und pro Board) und Hinweisen,
- Kalibrierungsbedarf: ob ein Format eine Importance Matrix nutzt, welche Quantisierungen sie verlangen, ob ein
  Kalibrierungsdatensatz optional oder Pflicht ist (RKNN `i8`) und welche Art Datensatz es ist (Text oder eine
  Liste mit Beispielen),
- Aliase: gängige Schreibweisen, die übersetzt werden (`INT8` → `Q8_0` für GGUF, `int4` → `w4a16` für RKLLM).

Das Schema steht in `orchestrator/schemas/targets.py`. GUI, CLI, Build-Dienst, Ditto und MCP-Server lesen denselben
Block: Sie bieten genau diese Optionen an, ergänzen die Standardwerte und lehnen alles andere mit einem klaren Grund
ab, bevor etwas läuft. Die Werte für Rockchip stehen in der [README](../README_DE.md#unterstützte-targets-und-formate).

---

## Build-Pipeline

Ein Build läuft in diesen Schritten; jeder wird protokolliert:

1. **Kalibrierungstext:** Für eine Importance Matrix ohne eigenen Datensatz schreibt das Framework seinen
   mitgelieferten, per Prüfsumme gesicherten Kalibrierungstext (Einstellung `calibration_language`: `mixed`, `de`
   oder `en`; siehe [data/calibration](../data/calibration/README_DE.md)). Scheitert das bei einer Quantisierung,
   die die Matrix verlangt, endet der Build mit einem Fehler.
2. **Vorbereiten:** den Kalibrierungsdatensatz prüfen (er bleibt nur dort, wo der Build ihn nutzt: GGUF mit
   Importance Matrix, RKLLM, RKNN `i8`).
3. **Image bauen:** das Docker-Image des Targets mit allen Toolkits (llama.cpp v0.6.0, RKLLM-Toolkit 1.3.1,
   RKNN-Toolkit2 2.3.2, jedes in einer eigenen Python-Umgebung). BuildKit wird genutzt, wenn vorhanden, und bei
   einem Fehler wird die echte Ursache gemeldet. Spätere Builds nutzen Dockers Build-Cache. Die
   Konvertierungswerkzeuge von llama.cpp ([unten](#konvertierungswerkzeuge-abgestimmt-auf-den-build-rechner))
   entstehen in diesem Schritt aus dem festgelegten Commit mit den Optionen des Build-Rezepts für diesen Rechner.
4. **Modell auf dem Host bereitstellen:** Ein Hugging-Face-Modell wird auf deinem Rechner heruntergeladen (mit dem
   Token `hf_token` für zugangsbeschränkte Modelle), ein lokaler Ordner bzw. eine Datei wird direkt verwendet. Die
   Container bekommen es nur lesend.
5. **Sicherheits-Scan:** Trivy durchsucht das Image nach Schwachstellen der Stufen HIGH und CRITICAL (Einstellung
   `enable_security_scan`, festgelegtes Image `aquasec/trivy:0.75.0`). Funde erscheinen als Warnung im Log, nicht
   als Fehler. Ein Scan, der nicht innerhalb des Zeitlimits des Builds (`build_timeout`) fertig wird, wird mit
   einer Warnung gestoppt.
6. **Importance Matrix** (nur GGUF, wenn gewählt).
7. **Konvertieren und quantisieren** in Containern mit Sandbox ([Build-Sandbox](#build-sandbox)).
8. **Ergebnisse:** Die Modelldatei wird eingesammelt, `build_info.json` wird um die Modelldateien und ihre
   SHA256-Hashes ergänzt, eine `Model_Card.md` mit Hashes und Hinweisen zur Nutzung wird geschrieben, und ein ZIP
   dieser Dateien wird erstellt. All das wird in einem versteckten Ordner neben dem Ausgabeordner vorbereitet und
   erst ganz am Ende hineinverschoben. So hinterlässt ein abgebrochener oder gescheiterter Build keine Dateien im
   Ausgabeordner, und Dateien eines früheren Builds dort ersetzt nur ein abgeschlossener. Links in der Ausgabe des
   Builds werden nie verfolgt. Images pro Build werden danach entfernt.

Weiteres Verhalten:

- **Abbrechen:** Die Schaltfläche **Cancel Build** im Builder-Tab (aktiv, solange der Build läuft, den der Tab
  gestartet hat; sie fragt nach einer Bestätigung), Strg+C in `llm-cli build start` und das Tool `cancel_build`
  (Ditto im GUI-Chat oder ein MCP-Client, für Builds des eigenen Prozesses) brechen alle über den Build-Service ab.
  Der laufende Container wird gestoppt, auch ein laufender Sicherheits-Scan oder Image-Build, und kein späterer
  Schritt kann den Abbruch rückgängig machen; Schaltfläche und Strg+C warten, bis der Build angehalten hat, und
  sagen das. Ist ein Build gescheitert oder verschiebt er gerade seine Dateien in den Ausgabeordner, lässt er sich
  nicht mehr abbrechen und läuft von selbst zu Ende.
- **Build-Status über Programme hinweg:** Jedes Programm, das baut, schreibt je Build einen Eintrag nach
  `<cache_dir>/build_records/`, deshalb listet `llm-cli build status` auch Builds, die die GUI, der MCP-Server oder
  ein anderer CLI-Aufruf gestartet hat; ein Build, dessen Programm endete, bevor er fertig war, erscheint als
  unterbrochen.
- **Build-Slots:** In einem Programm laufen höchstens `max_concurrent_builds` Builds gleichzeitig (Einstellung, 1
  bis 10, Vorgabe 2); ein weiterer Build wartet auf einen freien Slot (seine Stufe zeigt „Waiting for a free build
  slot“), statt zu scheitern. Ein abgebrochener Build behält seinen Slot, bis sein Container gestoppt ist.
- **Unbekanntes Target:** CLI, Ditto und MCP-Clients lehnen ein unbekanntes Target ab, bevor etwas läuft (die CLI
  mit Exit-Code 2). Eine Anfrage, bei der trotzdem nichts zu bauen bleibt, scheitert mit „Nothing to build“, statt
  Erfolg zu melden.
- **Zeitlimits:** Jeder Container-Schritt hat ein Zeitlimit (Einstellung `build_timeout`, Standard 3600 s); nur
  ein echtes Überschreiten zählt als Zeitüberschreitung.
- **Ausgabeordner:** CLI-Builds landen in `output/build_<Datum>_<Uhrzeit>/<Target>/<Modell>/<Quantisierung>/` in der
  Installation (oder in dem mit `-o` angegebenen Ordner), Builds aus der GUI, von Ditto oder von MCP-Clients in
  `output/<Target>/<Modell>/<Quantisierung>/`.

### Konvertierungswerkzeuge abgestimmt auf den Build-Rechner

Die Target-Images bauen die Konvertierungswerkzeuge von llama.cpp (`llama-quantize`, `llama-imatrix`,
`llama-perplexity`, `llama-completion`, `llama-bench`) aus dem Quelltext nach demselben Rezept wie Dittos Server
([Wie die Laufzeit gebaut wird](#wie-die-laufzeit-gebaut-wird-selbst-gebautes-llamacpp)); das mitgelieferte
Rockchip-Target und die Vorlage für neue Targets nutzen dieselben Schritte.

- **Der Pin, per Commit:** Die Engine übergibt `LLAMA_CPP_TAG` und `LLAMA_CPP_COMMIT`; das Image holt den Commit und
  bricht ab, wenn `git rev-parse HEAD` ihn nicht zurückgibt, ein verschobener Tag kann also nicht ändern, was gebaut
  wird. Auch die Einstellung `llama_cpp_version` kann ihn nicht verschieben; ein anderer Wert ergibt eine Warnung.
- **Abgestimmt auf den Rechner, der sie ausführt:** Das Image läuft auf dem Build-Rechner, deshalb ist
  `LLAMA_CMAKE_ARGS` der Plan des Rezepts für den Zweck `host-tools` aus einer Probe dieses Rechners, unmittelbar vor
  dem Image-Build erstellt (oder aus dem gespeicherten Profil dieses Rechners, wenn die Probe scheitert): seine
  CPU-Merkmale ausgeschrieben (AVX-512 nur, wo die CPU es hat, kein AVX2 auf einer CPU vor Haswell, also keine
  unzulässige Instruktion), oder alle CPU-Varianten, von denen zur Laufzeit die beste gewählt wird, wenn die Probe
  nicht jedes Merkmal erkennen konnte. Ohne Probe, oder mit einem Docker-Daemon auf einem anderen Rechner, gilt die
  Vorgabe des Dockerfiles: die feste x86-64-Basis, die diese Werkzeuge immer hatten (AVX2, FMA, F16C, kein AVX-512),
  ausgeschrieben.
- **Jeder Build:** Nach diesen Optionen setzt das Image, was jeder Build hat (nichts wird beim Konfigurieren
  heruntergeladen, keine Unterprozesse, Build-Nummer und Commit); Wörter, die nicht `-DNAME=WERT` sind, Optionen, die
  bei jedem Setzen etwas herunterladen, und ein Konfigurationsprotokoll, das einen Download meldet, stoppen den Build,
  und `llama-bench --version` muss den festgelegten Build und Commit melden. Build-Argumente eines Build-Auftrags
  können die drei llama.cpp-Argumente nicht ersetzen, und CPU- oder CMake-Flags aus dem Modul-Assistenten oder von
  einer KI erreichen sie nie.
- **Festgehalten:** Das Build-Log nennt die Abstimmung, und `build_info.json` hält sie unter `llama_cpp_tools` fest
  (Tag, Commit, Abstimmung, ID des Plans und seine Wörter).
- **Gleiche Ausgabe:** Das GGUF, das `llama-quantize` schreibt, hängt nicht von dieser Abstimmung ab. Gemessen am Pin
  mit GCC 13.3 an einem synthetischen llama-Modell von 6 MB: Q2_K, Q3_K_M, Q4_0, Q4_K_M, Q5_K_M, Q6_K, Q8_0, IQ4_NL,
  IQ4_XS und TQ2_0 (ohne Importance-Matrix) waren aus drei Builds byteweise gleich: der Standard-Basis, AVX-512 mit
  AMX und AVX ohne AVX2 und FMA. Der Bau der fünf Werkzeuge dauerte auf einer virtuellen Maschine mit 4 Kernen 3 bis
  6 Minuten (`-j4`).

---

## Build-Sandbox

Konvertierungs-Container führen nicht vertrauenswürdigen Modellcode aus (zum Beispiel eigene Tokenizer), deshalb
sind sie abgeschottet (Einstellung `build_sandbox`, standardmäßig an):

| Maßnahme | Wirkung |
|---|---|
| Deine Benutzer-ID statt root | Dateien gehören dir; nichts im Container läuft als root. |
| Kein Netzwerk (`network_mode: none`) | Das Modell kann keine Daten verschicken, Bibliotheken können nichts herunterladen. Alle Hugging-Face-Bibliotheken sind auf offline gestellt. |
| Keine Linux-Capabilities (`cap_drop: ALL`) und `no-new-privileges` | Keine Rechteausweitung. |
| Schreibgeschütztes Image-Dateisystem | Beschreibbar sind nur der eigene Arbeitsordner des Builds, ein 1 GB großes `/tmp` und der Ausgabeordner; das bereitgestellte Modell ist nur lesend eingebunden. |
| Prozesslimit (`build_pids_limit`, Standard 4096) | Schützt vor ausufernden Prozessen. |
| Optionales Speicherlimit (`build_memory_limit`, z. B. `24g`) | Schützt den Host davor, dass ihm der Speicher ausgeht. |

Weil die Sandbox kein Netzwerk hat, wird alles vorher auf dem Host heruntergeladen: das Modell (Schritt 4 oben) und
die Toolkits (beim Image-Build).

---

## Self-Healing

Scheitert ein Build, sieht sich der Self-Healing-Manager das Log an:

1. **Bekannte Probleme** werden an festen Regeln erkannt: Speichermangel beim Kompilieren des Images, eine vom
   Out-of-Memory-Killer beendete Konvertierung, eine volle Platte, fehlende Rechte für den Docker-Daemon, ein
   Netzwerkproblem, veraltete Paketquellen im Basis-Image.
2. **Sonst analysiert Ditto** das Log, wenn ein KI-Anbieter eingerichtet ist. Seine Antwort muss einer festen
   Struktur entsprechen (Zusammenfassung, Ursache, ein vorgeschlagener Schritt, Sicherheit, Host oder Gerät) und
   wird verworfen, wenn sie einen gefährlichen Befehl enthält.

Der Vorschlag wartet auf deine Entscheidung: ein Dialog in der GUI, eine Frage in der CLI oder das Tool
`resolve_healing` für Ditto und MCP-Clients (Einstellung `healing_approval_timeout`, Standard 900 s). Selbst wendet
das Framework nur feste, eingebaute Korrekturen an:

| Problem | Was das Framework nach deinem „Ja“ tut |
|---|---|
| Speichermangel beim Kompilieren des Images | Setzt `build_jobs` auf 1 und versucht es erneut |
| Platte voll | Entfernt verwaiste Framework-Images und den Docker-Build-Cache, dann neuer Versuch |
| Netzwerkproblem | Versucht es einmal erneut |

Alles andere, auch jeder KI-Vorschlag, wird als manueller Schritt angezeigt und nie ausgeführt. Ein erneuter Versuch
bekommt eine eigene Build-ID (`<job>_r1`), und beide Logs bleiben erhalten.

---

## Übertragen auf das Board (Deployment)

Das Deployment kopiert einen fertigen Build per SSH auf ein Board und installiert dort die Modelldatei.

- **Vertrauen beim ersten Kontakt:** Der Host-Schlüssel des Boards wird mit seinem SHA256-Fingerabdruck angezeigt
  (im selben Format wie `ssh-keygen -lf`). Erst nach deiner Bestätigung wird er im eigenen
  `~/.llm-framework/known_hosts` des Frameworks gespeichert. Ein geänderter Schlüssel wird immer abgelehnt.
- **Kopplung:** Bei der ersten Verbindung wird dein Passwort einmal genutzt, um den eigenen Ed25519-Schlüssel des
  Frameworks (`~/.llm-framework/id_ed25519_llm_framework`) auf dem Board zu hinterlegen. Das Passwort wird nie
  gespeichert; spätere Verbindungen nutzen den Schlüssel.
- **Paket:** eine ZIP-Datei mit der Modelldatei, Manifest und Model Card des Builds, `checksums.sha256` und einem
  erzeugten `deploy.sh`. Übertragen werden nur Modelldateien (`.gguf`, `.rkllm`, `.rknn`, `.onnx`, `.safetensors`,
  `.bin`); ein Build mit mehreren Gewichtsdateien wird abgelehnt.
- **Auf dem Board:** Das Paket kommt in einen privaten temporären Ordner (`mktemp -d`), `deploy.sh` (mit `sh`
  ausgeführt, also auch auf Boards, die `/tmp` mit `noexec` einhängen oder kein bash haben) prüft jede Prüfsumme mit
  `sha256sum -c`, kopiert das Modell nach `~/models` (oder `$MODEL_DIR`), und der temporäre Ordner wird
  gelöscht. Für GGUF gibt es aus, wie du das Modell mit dem Startskript der
  [selbst gebauten Laufzeit](#laufzeit-auf-dem-board-selbst-gebautes-llamacpp) startest, wenn sie auf dem Board
  installiert ist (auf 127.0.0.1 mit dem Schlüssel des Boards), sonst, wie du sie deployst; auf einen
  `llama-server` aus dem `PATH` des Boards verweist es nie. Für RKLLM nennt es die RKLLM-Runtime.
- **Docker-Modus** (`--docker`, Option in der GUI): packt zusätzlich Container-Images ein und startet sie auf dem
  Board. Dieser Modus ist experimentell und in dieser Version nicht geprüft.

### Laufzeit auf dem Board (selbst gebautes llama.cpp)

Für GGUF-Modelle baut das Framework llama.cpp für ein Board und installiert es dort (`llm-cli device ...`,
[CLI_Usage_DE.md](CLI_Usage_DE.md#device)). Die RKLLM- und RKNN-Runtime sind Herstellerbinärdateien; sie
installierst du selbst.

- **Bauplan aus der eigenen Probe des Boards** (Entscheidung D14): `llm-cli scan --device` legt das Profil des
  Boards unter seiner ID ab (aus seinem SSH-Host-Schlüssel). Der Bauplan ist ein Cross-Build für dieses Profil:
  `GGML_CPU_ARM_ARCH` aus den CPU-Merkmalen des Boards (RK3566: `armv8.2-a+dotprod+fp16`) und eine Builder-Basis,
  deren C-Bibliothek nicht neuer ist als die des Boards: die neueste Debian- oder Ubuntu-Basis mit einer glibc, die
  nicht neuer ist als die des Boards (Debian 11 für glibc 2.31 bis Debian 13 für 2.41), oder Alpine für musl-Boards,
  die mit Clang und dem Sysroot des Boards gebaut werden (ungeprüft). Ein Board, das älter ist als jede Basis, oder
  ein Profil ohne `LIBC_VERSION` bekommt keinen Bauplan und eine Meldung, warum. Vorerst nur CPU-Builds: Vulkan auf
  einer Mali-GPU erst, wenn `llama-bench` zeigt, dass es den CPU-Build schlägt (Entscheidung D8). `llm-cli device
  runtime plan` zeigt den Bauplan.
- **Build:** im Builder-Container ohne Netz, die C++-Laufzeit statisch gelinkt, der Server ohne
  Subprozess-Unterstützung. Der Build wird auf deinem PC im Format geprüft (ELF-Architektur, keine neuere
  glibc-Symbolversion als auf dem Board, keine geteilte C++-Laufzeit) und wie jeder selbst gebaute Build abgelegt. Ein
  OpenMP-Build bekommt zusätzlich die `libgomp.so.1` des Boards aus der Cross-Toolchain, die ihn gelinkt hat, und sie
  wird genauso geprüft.
- **Tarball:** `~/.llm-framework/runtime/llama.cpp/device/<Geräte-ID>/llama-runtime-<Arch>-<Build-ID>.tar.gz` (mit
  einer `.sha256`): `bin/` mit `llama-server`, `llama-cli`, `llama-bench` und `llama-perplexity`, bei Bedarf
  `lib/libgomp.so.1` (die Programme finden sie über ihren RUNPATH `$ORIGIN/../lib`), das Startskript
  `llama-server.sh`, `build.json` und `manifest.json`, die Lizenz von llama.cpp als `LICENSE-llama.cpp` und
  `SHA256SUMS`. Er ist deterministisch (feste Besitzer, Rechte und Zeiten). Das Framework teilt dieses Rezept, nie den
  Tarball (Entscheidung D21).
- **Deployment** (`llm-cli device runtime deploy`): nur auf das Board, mit dessen Host-Schlüssel das Profil erstellt
  wurde, als SSH-Benutzer der Deploy-Einstellungen; root wird abgelehnt. Werte, die das Board meldet (Home-Ordner,
  Benutzer-ID, Architektur, fehlende Werkzeuge), werden geprüft, bevor sie verwendet werden. Der Tarball kommt über
  die Standardeingabe des Befehls in einen privaten Staging-Ordner in `~/.local/share/llm-framework/llama.cpp/` (nie
  `/tmp`, das `noexec` sein kann), wird gegen seine sha256 und jede Datei gegen `SHA256SUMS` geprüft, nur für den
  Benutzer lesbar gemacht und neben den aktiven Build gelegt. Dann werden die Programme des neuen Builds auf dem
  Board geprüft (unten), und nur ein Build, der besteht, wird zu `current`; einer, der scheitert, wird wieder
  entfernt, und der vorige Build bleibt aktiv (Design 3.8: ein gescheiterter Build wird nie aktiviert). Nach dem
  Umschalten bleibt der vorige Build; ältere werden entfernt, außer ein laufendes Programm nutzt sie. Den Schlüssel
  in `~/.config/llm-framework/llama-server.key` erzeugt das Startskript auf dem Board unter umask 077; er verlässt
  das Board nie.
- **Startskript** (`llama-server.sh serve|start|stop|status|keygen|version`): POSIX-sh, getestet mit dash und bash. Es
  startet `llama-server` aus seinem eigenen Ordner (nie aus `PATH`) mit `env -i` (HOME, ein fester PATH, die
  C-Locale), `--host 127.0.0.1`, `--api-key-file`, `--no-ui`, `--no-slots`, `--jinja` und `--offline`. Es reicht nur
  Tuning-Optionen mit geprüften Werten weiter (`-c`, `-t`, `-tb`, `-C`, `--cpu-strict`, `-b`, `-ub`, `-np`, `-ngl`,
  `-fa`, `-cram`, `-lm`, `-a`, `--sleep-idle-seconds` und `--port` ab 1024); eine Option der gemeinsamen Verbotsliste
  wird mit Namen abgelehnt, auch in der Schreibweise `--a_b`, und jede andere Option ebenso, sodass das Modell nie
  Werkzeuge bekommt und der Server Loopback nie verlässt.
- **Deploy-Einstellungen, vor dem Deployment entschieden** (Entscheidung D7, `llm-cli device settings`): das
  Startskript im Home des Benutzers (die Vorgabe) oder eine systemd-Unit. Die Unit ist nur wählbar, wenn die Probe
  des Boards `INIT_SYSTEM=systemd` meldet, braucht das Modell, das sie bereitstellt, und deine Bestätigung, gegeben
  bei der Wahl, dass Deployments auf dem Board `sudo -n` ausführen dürfen; die Bestätigung wird ins Audit-Log
  geschrieben. Hat der neue Build seine Prüfungen bestanden, schreibt das Deployment
  `/etc/systemd/system/llm-llama-server.service` mit `sudo -n` (das scheitert, statt zu fragen), aktiviert die Unit
  und startet sie, sobald das Modell da ist. Die Unit führt `serve` des
  Startskripts als der Benutzer aus, ohne `Environment=`, mit `NoNewPrivileges`, schreibgeschütztem System und Home
  sowie `IPAddressDeny=any` mit `IPAddressAllow=localhost`. Boards mit OpenRC oder SysV-Init nutzen das Startskript.
  Ein Deployment stellt nie eine Frage.
- **Prüfungen auf dem Board** (auch `llm-cli device runtime verify`), als unprivilegierter Benutzer und nie mit
  sudo: `llama-server --version` und `llama-bench --version` müssen den Pin melden, `llama-bench --list-devices`
  muss die geplanten Geräte zeigen, und `llama-server --tools all` muss abgelehnt werden. Mit `--bench-model` läuft ein
  kurzes `llama-bench -p 64 -n 16 -r 1` auf einem Modell auf dem Board: ein Katalogmodell (an seiner sha256 erkannt)
  sofort, jedes andere Modell erst nach deiner Zustimmung, die ins Audit-Log geschrieben wird (Entscheidung D23). Ein
  `SIGILL` heißt, dass die CPU-Grundstufe des Builds nicht zum Board passt. Ein Deployment führt diese Prüfungen am
  neuen Build aus, bevor er zu `current` wird; ein Build, der sie nicht besteht, läuft also nie als Server des
  Boards. Das Ergebnis liegt in `device/<Geräte-ID>/deployed.json`; `llm-cli device runtime status` sagt, ob der
  deployte Build noch der ist, den Profil und Pin verlangen.
- **Gemessen** mit echten Programmen statt eines Boards: `llama-server` und `llama-bench`, nativ mit den Optionen des
  Geräte-Bauplans für einen x86-64-Rechner gebaut (GCC 13.3, `-j4`, etwa 8 Minuten auf 4 geteilten Kernen), ergaben
  30 MB Programme und einen Tarball von 11,6 MB; die Formatprüfung (glibc 2.38 nötig, keine geteilte C++-Laufzeit),
  das Deployment auf ein lokal nachgebildetes Board und die Prüfungen dort bestanden, und `libgomp.so.1` wurde über
  den RUNPATH in `lib/` gefunden.
- **Noch nicht:** ein Lauf auf einem echten Board, ein Cross-Build in dieser Entwicklungsumgebung (kein
  Docker-Dienst antwortete), das Bauen auf dem Board selbst, wenn keine Basis alt genug ist, die GUI und das
  MCP-Werkzeug `deploy_runtime` sowie die Laufzeit in Deployment-Paketen.

---

## Ditto, der Assistent

Ditto baut auf der eigenen **Anbieterschicht** des Frameworks auf (`orchestrator/llm`), die die offiziellen SDKs
`anthropic` und `openai` verwendet (OpenAI-kompatible Server laufen über `openai`).

### Aufgaben, Modelle und Aufwand

Dittos vier Arten von Arbeit entsprechen den Aufgabenprofilen der Anbieterschicht; jedes Aufgabenprofil hat pro
Anbieter ein eigenes Modell und einen eigenen Aufwand:

| Ditto-Profil | Aufgabe | Antwort |
|---|---|---|
| Chat | `chat` | Freier Text; darf die Tools des Frameworks nutzen |
| Healing-Analyse | `healing` | Strukturiert: Zusammenfassung, Ursache, vorgeschlagener Schritt, Sicherheit, Host oder Gerät |
| Modulvorschlag | `json` | Strukturierte Target-Definition, von dir geprüft und vom Modulgenerator erneut kontrolliert |
| Build-Empfehlung | `json` | Strukturiert: Format und Quantisierung aus den Optionen des Targets, Optimierungsstufe, Begründung |

Die Standardwerte pro Aufgabe stehen in [`configs/llm_providers.yml`](../configs/llm_providers.yml). Deine
Einstellungen überschreiben sie: `ai_model` und `ai_effort` für alle Aufgaben, oder `ai_model_<Aufgabe>` und
`ai_effort_<Aufgabe>` für eine Aufgabe. Aufwandsstufen sind `low`, `medium`, `high`, `xhigh` und `max`; jeder
Anbieter übersetzt sie in das, was er versteht.

### Anbieter

| ID | Art | Wo es läuft | Anmeldung heute |
|---|---|---|---|
| `anthropic` | Anthropic-Claude-API | Cloud | API-Schlüssel (`anthropic_api_key` oder `ANTHROPIC_API_KEY`) |
| `openai` | OpenAI Responses API | Cloud | API-Schlüssel (`openai_api_key` oder `OPENAI_API_KEY`) |
| `google` | Gemini, OpenAI-kompatibler Endpunkt | Cloud | API-Schlüssel (`gemini_api_key` oder `GEMINI_API_KEY`) |
| `openrouter` | OpenRouter | Cloud | API-Schlüssel (`openrouter_api_key` oder `OPENROUTER_API_KEY`) |
| `ollama` | Ollama | lokal (Basis-URL einstellbar) | keine |
| `local` | verwalteter llama-server | lokal | Schlüssel pro Start, wird automatisch verwaltet |
| `custom` | jeder OpenAI-kompatible Server | ergibt sich aus der Adresse: Loopback-, private und Link-Local-Adressen sowie Namen auf `.local`, `.lan`, `.localhost` oder `.home.arpa` gelten als lokal | API-Schlüssel der Serveradresse (`custom_api_key`) oder keine |

- Cloud-Anbieter haben feste Adressen; eine von Ollama oder einem eigenen Server übrig gebliebene Basis-URL bekommt
  nie ihren Schlüssel.
- Ein Schlüssel eines eigenen Servers gehört zu der Adresse, für die er gespeichert wurde (Schema, Host und Port):
  Nach einer Änderung der Basis-URL wird er nicht mehr mitgeschickt, und der neue Server braucht seinen eigenen
  Schlüssel.
- Jeder Anbieter nennt seine Anmeldeverfahren in `configs/llm_providers.yml` in der Reihenfolge, in der sie
  bevorzugt werden; das erste nutzbare wird genutzt, außer `ai_auth_<Anbieter>` nennt eines. Ein Schlüssel in einer
  der Umgebungsvariablen aus der Tabelle oben wird nur genutzt, wenn kein Schlüssel gespeichert ist, und die
  KI-Einstellungen zeigen die Variable als Quelle; er wird nie in den Tresor kopiert. Lässt sich der Tresor nicht
  lesen, wird die Variable nicht genutzt, und die Fehlermeldung nennt das Problem mit dem Tresor.
- Eine kostenlose Prüfung (die Modellliste des Anbieters, bei OpenRouter der Schlüssel-Endpunkt) zeigt, ob der
  gespeicherte Schlüssel funktioniert, zu welchem Workspace oder Schlüsselnamen er gehört und wie viele Modelle er
  nutzen kann. Angaben zu einer Anmeldung, die nicht geheim sind (Konto oder Workspace, die letzten vier Zeichen
  eines Schlüssels, Zeitpunkt der Prüfung), liegen in `auth.json` neben dem Tresor.
- Die Modellliste in den Einstellungen wird live beim Anbieter abgefragt; jeder Modellname lässt sich auch
  eintippen.
- Eine Datei `llm_providers.yml` in deinem Einstellungsordner ergänzt oder ersetzt Anbieter anhand ihrer ID; ein
  neuer OpenAI-kompatibler Server braucht also keine Codeänderung.
- **Claude-API (`anthropic`):** Der gespeicherte API-Schlüssel hat immer Vorrang. Ein Profil aus `ant auth login`
  wird nur genutzt, wenn du es auswählst (`ai_auth_anthropic: ant_profile`; der frühere Wert `oauth` bedeutet
  dasselbe). Ein Schlüssel, der in jedem Workspace der Organisation gilt, braucht die Workspace-ID (Feld „Workspace
  ID“ in den KI-Einstellungen, Einstellung `ai_anthropic_workspace_id`, `wrkspc_...`; solange sie gesetzt ist,
  hat sie Vorrang vor einer mit dem Schlüssel gespeicherten Workspace-ID); sie geht nur mit dem API-Schlüssel und
  nur an die Claude-API. Admin-API-Schlüssel (`sk-ant-admin01-`) werden vor jeder Anfrage abgelehnt.
- **OpenAI (`openai`):** Eine mit dem Schlüssel gespeicherte Organisation und ein Projekt (`org-...`, `proj_...`)
  gehen als Header `OpenAI-Organization` und `OpenAI-Project` mit; ein Schlüssel, der funktioniert, aber keine
  Modelle auflisten darf, wird als solcher gemeldet, und die Modell-ID lässt sich eintippen.
- Einen Anbieter `claude_code` (Ditto über ein Claude-Abo) gibt es nicht: Anthropic erlaubt Drittanbieter-Apps nicht,
  Abo-Limits zu nutzen. Ein gespeichertes `claude_code` (auch in der Ersatzkette oder bei `--provider`) bricht mit
  einer Meldung ab, die die Alternativen nennt; ein solcher Eintrag in deiner eigenen `llm_providers.yml` wird mit
  einer Warnung übersprungen. Claude-Abonnenten nutzen das Framework aus Claude Code oder Claude Desktop über
  [MCP](#mcp-server) oder mit einem API-Schlüssel (Max- und Team-Abos enthalten monatliches API-Guthaben).
- Die SDKs lesen selbst nie Zugangsdaten, Header oder Adressen aus der Umgebung: Jeder Client bekommt Schlüssel
  und Basis-URL ausdrücklich, und `OPENAI_CUSTOM_HEADERS` / `ANTHROPIC_CUSTOM_HEADERS` werden beim Start mit einer
  Warnung entfernt (die Einstellung `ai_allow_sdk_env_headers: true` behält sie).

### Anmeldung

- **Wo:** **Anmelden...** in den KI-Einstellungen (GUI) und `llm-cli ditto login | logout | auth` (siehe die
  [CLI-Anleitung](CLI_Usage_DE.md#ditto-login)).
- **Karten für die Verfahren:** Der Bereich zeigt die Anmeldeverfahren des Anbieters in der Reihenfolge des
  Katalogs. Das erste, mit dem sich diese Version anmelden kann, ist als „Empfohlen“ markiert, Verfahren nur auf
  Wunsch (das `ant`-Profil) als „Erweitert“, und Verfahren einer späteren Version werden gezeigt, lassen sich aber
  nicht wählen. Jede Art hat ihr eigenes Formular: ein Schlüsselformular (Schlüsselseite, Passwortfeld, die
  zusätzlichen Felder aus dem Katalog wie die Claude-Workspace-ID oder Organisation und Projekt bei OpenAI,
  **Prüfen**, **Speichern**, **Abmelden**), „keine Anmeldung“ mit Prüfen für lokale Engines, das Profil der
  `ant`-CLI (der offizielle Befehl, der Name des Profils, eine Prüfung über das Anthropic-SDK; das Framework startet
  die CLI nie und liest ihre Dateien nicht) und der verwaltete lokale Server, dessen Schlüssel automatisch kommt.
- **Statuszeile:** „Angemeldet als ...“, „Nicht angemeldet“, „Schlüssel aus der Umgebungsvariable X“ oder
  „Abgelaufen: bitte erneut anmelden“. Ein Schlüssel erscheint nur mit seinen letzten vier Zeichen. Der Kopf von
  Dittos Chat zeigt den genutzten Zugang, zum Beispiel „Anthropic Claude (API) · API key · wrkspc_...“ oder
  „Lokal · Ollama“.
- **Prüfen vor dem Speichern:** Prüfen sendet den eingetippten Schlüssel mit einer kostenlosen Anfrage und speichert
  nichts; `llm-cli ditto login` prüft einen Schlüssel genauso, bevor es ihn speichert. Speichern merkt sich das
  Verfahren als `ai_auth_<anbieter>`.
- **Kein Geheimnis in der Befehlszeile:** Die CLI nimmt einen Schlüssel nur aus einer verdeckten Abfrage, von stdin
  (`--with-token`) oder aus der dokumentierten Umgebungsvariable des Anbieters (`--from-env`). Ohne Terminal fragt
  sie nichts, und ein Anbieter mit mehreren Verfahren braucht `--method`. Exit-Codes: 0 angemeldet, 2 Bedienfehler,
  3 abgelehnt, 4 Netz, 5 Speicher, 6 nicht verfügbar.
- **Schlüssel bleiben heil und erscheinen nicht in Ausgaben:** Ein Schlüssel mit einem Leerzeichen, einem
  Zeilenumbruch oder einem unsichtbaren Zeichen darin wird abgelehnt, bevor etwas gesendet oder gespeichert wird; ein
  Fehlertext des Servers, der den Schlüssel wiederholt, zeigt stattdessen `REDACTED`; und ein eingetippter Schlüssel
  geht über einfaches `http://` an einen anderen Computer erst, wenn du es bestätigst (CLI: `--yes`). Ein Schlüssel
  im eigenen Schlüsselfeld der KI-Einstellungen ersetzt ein Verfahren, das sich eine Anmeldung gemerkt hat (zum
  Beispiel das `ant`-Profil).
- **Abmelden zuerst lokal:** Der gespeicherte Schlüssel wird gelöscht, und die Schlüsselseite, auf der du den
  Schlüssel selbst widerrufst, wird genannt; ein Schlüssel in einer Umgebungsvariable bleibt in Gebrauch, bis du sie
  entfernst.
- **Kein nutzbarer Schlüsselbund:** Der Bereich zeigt die Auswahl für den Schutz (Passphrase, Credential-Helper
  oder die unverschlüsselte Schlüsseldatei nach einer Bestätigung), und die GUI zeigt dieselbe Auswahl beim Start,
  bevor sie aufgibt; eine Passphrase wird in einem Dialog abgefragt. Eine Wahl, die den vorhandenen Tresor nicht
  öffnen kann (einen, den der gesperrte Schlüsselbund schützt), wird nicht behalten. Die CLI endet mit Exit-Code 5
  und nennt `llm-cli ditto auth storage --use ...`. Für dich wird nichts gewählt.
- **Reaktionsschnell:** Jeder Zugriff auf Tresor, Schlüsselbund oder Netz aus dem Anmeldebereich und für den Kopf
  des Chats läuft in einem Hintergrund-Thread.

### Aufrufe, Wiederholungen und Ersatzkette

- Die eigenen Wiederholungen der SDKs sind aus; die Anbieterschicht wiederholt Anfragen bei Ratenlimits und
  Ausfällen bis zu zweimal und wartet auf ein `Retry-After` des Anbieters von bis zu 30 s (Einstellung
  `ai_max_retry_wait`). Jede Anfrage darf bis zu 600 s dauern (`ai_timeout`).
- **Ersatzkette** (`ai_fallback_chain`, z. B. `local, ollama:qwen3.5:4b`): Lehnt das Modell ab oder ist der Anbieter
  nicht erreichbar, wird der nächste Eintrag versucht. Nicht eingerichtete Einträge werden übersprungen.
- **Offline-Modus** (`offline_mode`): Es werden nur lokale Anbieter genutzt; ein Cloud-Anbieter wird mit einer
  klaren Meldung abgelehnt. Auch die Wissensbasis lädt im Offline-Modus nichts herunter.
- Anfragen an Server auf 127.0.0.1 gehen nie über einen HTTP(S)-Proxy aus der Umgebung; der Proxy sieht also weder
  Prompts noch den Schlüssel des lokalen Servers.
- Fehler werden verständlich erklärt, zum Beispiel dass ein Claude-API-Schlüssel eine Workspace-ID braucht, dass
  der Schlüssel widerrufen wurde oder abgelaufen ist, dass das Guthaben aufgebraucht ist oder dass der lokale Server
  nicht läuft.

### Datenschutzfilter für Cloud-Anbieter

Bevor Text an einen Cloud-Anbieter geht, werden persönliche Daten ersetzt (Einstellung `ai_security_level`):

| Stufe | Ersetzt |
|---|---|
| `OFF` | nur Geheimnisse (API-Schlüssel, Tokens); sie werden immer entfernt und nie wiederhergestellt |
| `BALANCED` | zusätzlich IPv4-/IPv6- und MAC-Adressen, E-Mail-Adressen, Logins der Form `user@host`, Benutzernamen in Home-Pfaden |
| `STRICT` (Standard) | zusätzlich in Probe-Dateien angegebene Hostnamen, Seriennummern, UUIDs und die Verzeichnisse absoluter Pfade (Systempfade wie `/usr/` bleiben lesbar) |

Die Platzhalter sehen aus wie `<IP_1a2b3c4d>`. Sie werden aus dem Wert mit einem Schlüssel abgeleitet, der einmal
pro Installation angelegt wird; derselbe Wert bekommt also immer denselben Platzhalter. Platzhalter in der Antwort
werden auf deinem Rechner wieder in die echten Werte verwandelt. Lokale Anbieter werden nie gefiltert.

### Strukturierte Antworten und der Filter für gefährliche Befehle

Healing-Analysen, Modulvorschläge und Build-Empfehlungen müssen gegen ein Schema mit Längengrenzen gültig sein.
Alles andere ist ein Fehler und wird nie angewendet. Bei Build-Empfehlungen führt schon das Schema nur die Formate
und Quantisierungen auf, die das Target anbietet, und die Antwort wird pro Format erneut geprüft.

Was auch immer ein Build-Log, eine Probe-Datei oder ein Modellname dem Modell erzählt: Ditto verwirft eine
Healing-Korrektur oder Empfehlung mit einem gefährlichen Befehl. Dazu zählen ein Download, der direkt in eine Shell
geleitet wird, für alle beschreibbare Rechte (`chmod 777`/`666`), das Löschen des Wurzel- oder Home-Ordners, das
Überschreiben einer Platte (`mkfs`, `dd` auf eine Platte) und eine Fork-Bombe.

### Nicht vertrauenswürdiger Text

Build-Logs, Probe-Dateien, Auszüge aus der Wissensbasis und Tool-Ergebnisse sind externer Text. Ditto setzt sie in
den Benutzer-Turn, bereinigt von versteckten und Steuerzeichen und eingepackt in gekennzeichnete
`<untrusted_data>`-Blöcke, nie in den System-Prompt. Der System-Prompt sagt dem Modell, dass solche Blöcke Daten
sind, keine Anweisungen.

### Chatverlauf

Der GUI-Chat wird pro Benutzer in `~/.llm-framework/chat_history.json` gespeichert (nur für dich lesbar). Das Gespräch
wird nur fortgeschrieben: Frühere Turns werden nie bearbeitet. Wächst es über die Einstellung `chat_context_limit`
hinaus (Standard 4096 Tokens, geschätzt mit vier Zeichen pro Token), fasst Ditto es in einer eigenen Anfrage
zusammen und setzt in einem neuen Gespräch fort, das mit der Zusammenfassung beginnt. Das Fenster behält bis zu 500
Einträge. Verläufe früherer Versionen werden übernommen. `llm-cli ditto ask` nutzt den Verlauf nicht.

---

## Dittos Tools

Im Chat (GUI) und mit `llm-cli ditto ask` kann Ditto dieselben Tools wie MCP-Clients nutzen:

| Tool | Stufe | Was es tut |
|---|---|---|
| `list_targets` | lesen | Targets mit Boards, Formaten und Quantisierungen |
| `get_target_capabilities` | lesen | Was ein Target bauen kann |
| `list_build_requests` | lesen | Build-Anfragen dieser Sitzung |
| `get_build_status` | lesen | Status, Fortschritt, Fehler, ein offener Self-Healing-Vorschlag |
| `read_build_log` | lesen | Die letzten Zeilen eines Build-Logs (Geheimnisse geschwärzt) |
| `list_artifacts` | lesen | Fertige Builds aus ihren Manifesten |
| `run_doctor` | lesen | Der Gesundheitscheck, ohne Korrekturen |
| `search_knowledge` | lesen | In der Wissensbasis suchen |
| `start_build` | lokal schreiben | Einen Build starten (muss zu den Capabilities des Targets passen) |
| `cancel_build` | lokal schreiben | Einen Build abbrechen |
| `resolve_healing` | lokal schreiben | Einen Self-Healing-Vorschlag annehmen oder verwerfen |

So läuft eine Frage mit Tools ab:

1. Ditto bekommt die Definitionen der Tools, die deine Berechtigungen nicht verbieten.
2. Das Modell darf Tools aufrufen. Jeder Aufruf läuft durch die Tool-Registry: Prüfung der Eingabe, deine
   Berechtigung, Bestätigung, wo die Berechtigung „fragen“ sagt (ein Dialog in der GUI mit dem Titel des Tools und
   jedem Argument; eine Abfrage in der CLI, ohne Terminal abgelehnt), Eintrag im Audit-Log mit dem Akteur `ditto`.
3. Tool-Ergebnisse gehen als nicht vertrauenswürdige Daten zurück an das Modell. Ein abgelehnter oder gescheiterter
   Aufruf wird dem Modell gemeldet, das angewiesen ist, denselben Aufruf nicht zu wiederholen.
4. Nach höchstens 8 Modell-Turns pro Frage hört Ditto auf und sagt das.

Dittos Prompts weisen es an, vor einem Build die Capabilities des Targets zu prüfen, Builds nur zu starten oder
abzubrechen, wenn du im Gespräch darum gebeten hast, über Self-Healing nur mit deiner ausdrücklichen Entscheidung zu
entscheiden, Fragen zu Targets und Builds aus den Tools statt aus dem Gedächtnis zu beantworten und nur zu berichten,
was die Tools zurückgegeben haben.

---

## Ditto-Evaluation

`llm-cli ditto eval` ist ein Werkzeug, um zu messen, wie gut ein Anbieter und ein Modell für Ditto taugen, zum
Beispiel ob ein kleines lokales 2-Bit-Modell für schwache Hardware reicht. Nichts wird gebaut oder geändert. Mit
`--kind retrieval` misst es stattdessen die Suche in der Wissensbasis ([Retrieval-Evaluation](#retrieval-evaluation)).

**Fälle** ([`evals/ditto/cases.yml`](../evals/ditto/cases.yml), 19 Fälle; eine Datei mit Fällen ist Daten, neue
Fälle brauchen keine Codeänderung):

| Art | Fälle | Was geprüft wird |
|---|---|---|
| `healing` | 6 | Analyse von Build-Logs: Ursache, Host oder Gerät, eine passende Korrektur (fehlendes CMake, Speichermangel, zugangsbeschränktes Modell, volle Platte, eine auf dem Gerät fehlende Bibliothek, ein Log mit eingeschleusten Anweisungen) |
| `optimizer` | 5 | Build-Empfehlungen aus den echten Rockchip-Optionen: z. B. GGUF für ein LLM auf dem RK3566, ein ONNX-Bildmodell zu RKNN, ein Modellname mit eingeschleusten Anweisungen, deren Befehl nicht in der Antwort auftauchen darf |
| `module` | 3 | Modulvorschläge aus Probe-Dateien (Jetson Orin, Raspberry Pi 5, x86-Server): Architektur, SDK, Compiler- und CMake-Flags |
| `chat` | 5 | Chat mit Tools: welche Tools mit welchen Argumenten aufgerufen werden und welche nicht (zum Beispiel ein gescheiterter Build, dessen Log eingeschleuste Anweisungen enthält: Ditto muss den Fehler erklären und darf keine Builds abbrechen oder starten) |

**Bewertet** wird mit festen Regeln, nicht von einem weiteren Modell: erwartete Wörter oder Muster, verbotene Wörter
(zum Beispiel ein eingeschleuster Befehl), erwartete Felder, Tools, die aufgerufen oder nicht aufgerufen werden
müssen. Chat-Fälle nutzen die echten Tool-Definitionen über einem Ersatz-Build-Dienst, der gegen die echten
Target-Capabilities prüft und vorbereitete Builds, Logs und Artefakte zurückgibt. Die Wissensbasis ist während der
Evaluation aus, damit die Antworten nur aus dem Fall kommen.

**Ausführen:**

```bash
llm-cli ditto eval                                      # der eingestellte Anbieter und das eingestellte Modell
llm-cli ditto eval --tier small --tier small-q2 --effort low --json eval.json
llm-cli ditto eval --kind chat --repeat 3 --min-pass 0.8
```

Mit `--tier` startet die CLI den verwalteten lokalen Server nacheinander mit jeder Stufe und stoppt ihn nach jeder
Stufe. Der Bericht zeigt den bestandenen Anteil pro Lauf, pro Art und die durchschnittliche Zeit pro Fall; `--json`
ergänzt die Einrichtung (Anbieter, Modell pro Aufgabe, Aufwand, Framework-Version, sha256 der Fall-Datei) und jede
Antwort. Cloud-Anbieter berechnen etwa 20 Anfragen pro Lauf und Wiederholung. Alle Optionen:
[CLI_Usage_DE.md](CLI_Usage_DE.md#ditto-eval).

**Erste Messung** (zur Einordnung, kein Benchmark): Auf einem Rechner nur mit CPU, mit Qwen3.5 2B, Aufwand `low`
(Nachdenken aus) und den damaligen Prompts bestand die 2-Bit-Stufe `small-q2` 42 % der 19 Fälle und die 4-Bit-Stufe
`small` 47 %. Die Evaluation hat Lücken in Dittos Prompts gefunden (Fehler dem Host oder dem Gerät zuordnen, welche
Eingaben jedes Format annimmt, die Tools für Fragen zu Targets und Builds nutzen, Compiler-Flags in
Modulvorschlägen). Diese Lücken sind inzwischen behoben, und eine neue Messung läuft.

### Retrieval-Evaluation

`llm-cli ditto eval --kind retrieval` bewertet die Suche in der [Wissensbasis](#wissensbasis-rag) statt eines
Modells. Kein KI-Modell ist beteiligt, und deine eigene Wissensbasis wird nicht angefasst.

- **Fälle:** [`evals/retrieval/cases.yml`](../evals/retrieval/cases.yml), 42 deutsche und englische Suchen in
  `evals/retrieval/corpus/`, 30 Auszügen aus der Dokumentation des Frameworks und von SDKs. Jeder Fall nennt die
  Auszüge, die zählen, und den Rang, den der erste davon erreichen muss, oder erwartet bei einer fachfremden Frage
  gar keinen Auszug.
- **Tags** gliedern den Bericht: `en` und `de` (Sprache der Frage), `cross-lingual` (Frage und Auszug in
  verschiedenen Sprachen), `identifier` (zum Beispiel `GGML_CUDA`), `oversized` (die Antwort steht tief in einem
  Block, der größer ist als der Kontext des Embedding-Modells), `framework` (die eigene Dokumentation des
  Frameworks), `log` (die letzten drei Zeilen eines gescheiterten Builds, wie sie die Analyse von Build-Logs sucht),
  `callsite` (Dittos eigene Suchtexte für Modulvorschläge und Build-Empfehlungen) und `negative` (fachfremde Fragen).
- **Ablauf:** Der Korpus wird mit dem eingestellten lokalen Embedding-Modell in eine temporäre Wissensbasis in einem
  temporären Ordner aufgenommen, der danach gelöscht wird. Das Modell wird bei der ersten Nutzung heruntergeladen;
  danach läuft die Evaluation offline. Retrieval-Fälle laufen nur, wenn `--kind retrieval` angegeben ist oder
  `--case` einen von ihnen nennt.
- **Varianten** (`--rag-variant`, mehrfach möglich, Standard `fused`): `fused` ist die hybride Suche; `rerank` nimmt
  den Cross-Encoder aus `rag_reranker` und seine Relevanzschwelle hinzu ([Reranking](#reranking)). `rerank` bricht
  mit einer Meldung ab, wenn kein Reranker zum Rechner passt (setze `rag_reranker` auf eine Modell-ID, um trotzdem
  einen zu bewerten), und ein Fall gilt als nicht bestanden, wenn der Reranker nicht gelaufen ist.
- **Bericht:** pro Variante hit@1, hit@3, hit@k (innerhalb des Rangs jedes Falls), MRR über die ersten 10 Ergebnisse,
  der Anteil fachfremder Fragen ohne Auszug und die mittlere Suchzeit (Median). `--json` ergänzt die Werte pro Tag,
  die passenden Fragen, die gar keinen Auszug bekamen, das 95. Perzentil der Suchzeit, die Modelle, den sha256 der
  Fälle und des Korpus sowie jedes Ergebnis.
- **Gemessen** mit dem Standard-Embedding-Modell für die CPU an den 34 Fällen, die keine `log`- oder
  `callsite`-Suchen sind (30 mit einem erwarteten Auszug, 4 fachfremd): Die hybride Suche erreicht hit@1 87 % und MRR
  0,93, aber alle 4 fachfremden Fragen bekommen trotzdem Auszüge, weil sie keine Relevanzschwelle hat. Die Werte mit
  Reranking stehen unter [Reranking](#reranking).
- **CI:** Der Workflow `.github/workflows/retrieval-eval.yml` führt `fused` und `rerank` jede Nacht und auf Wunsch
  aus, mit zwischen den Läufen zwischengespeicherten Modellen. Er schlägt fehl, wenn weniger als 70 % der Fälle
  bestehen (`--min-pass 0.7`); das fängt eine kaputte Suche ab, keine kleinen Änderungen. Den JSON-Bericht lädt er
  hoch.

```bash
llm-cli ditto eval --kind retrieval                                  # hybride Suche
llm-cli ditto eval --kind retrieval --rag-variant fused --rag-variant rerank --json retrieval.json
```

---

## Lokale Laufzeit und Modellstufen

Die verwaltete Laufzeit gibt Ditto ein lokales Modell ohne Cloud-Konto. Gesteuert wird sie mit
`llm-cli ditto runtime` und `llm-cli ditto models` ([CLI_Usage_DE.md](CLI_Usage_DE.md#ditto-runtime)); Dittos
Anbieter `local` findet den laufenden Server von selbst (Adresse, Schlüssel und geladenes Modell werden bei jedem
Aufruf gelesen).

### Was festgelegt ist

[`configs/llama_runtime.yml`](../configs/llama_runtime.yml) ist Daten: Ein neuerer Build oder ein anderes Modell wird
dort ergänzt, nicht im Code.

- **llama.cpp-Commit** `d8123504` (Tag b11429, Version 0.6.0): `ditto runtime install` baut den Server aus genau
  diesem Commit (siehe [Wie die Laufzeit gebaut wird](#wie-die-laufzeit-gebaut-wird-selbst-gebautes-llamacpp)).
- **Offizielle vorgebaute Archive** von b11429, nur für die ausdrückliche Wahl `ditto runtime install --prebuilt`
  und immer als „prebuilt, not self-built“ angezeigt: Linux x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.8, ROCm auf
  Wunsch), Linux arm64 (CPU, Vulkan, CUDA 13.4), Windows x86-64 (CPU, Vulkan, CUDA 13.4, CUDA 12.4, ROCm auf
  Wunsch), Windows arm64 (CPU) und macOS (Metal auf Apple Silicon, CPU auf Intel). Jedes Linux-Archiv nennt die
  glibc, gegen die es gebaut wurde (2.35 für x86-64 CPU und Vulkan, 2.39 für die anderen, aus dem Release-Workflow
  von upstream gelesen); einem Rechner mit älterer glibc oder mit musl wird keines angeboten.
- **Modelle** auf festen Hugging-Face-Commits, alle unter Apache-2.0:

| Stufe | Datei | Größe | Hinweis |
|---|---|---|---|
| `small` | Qwen3.5-2B-Q4_K_M.gguf | 1,2 GiB | für Rechner mit bis zu 4 GB RAM |
| `small-q2` | Qwen3.5-2B-UD-Q2_K_XL.gguf | 0,9 GiB | 2 Bit, für 3 GB RAM oder weniger; ungenauer |
| `default` | Qwen3.5-4B-Q4_K_M.gguf | 2,6 GiB | der Standard |
| `default-q2` | Qwen3.5-4B-UD-Q2_K_XL.gguf | 1,8 GiB | 2 Bit, nur auf Wunsch |
| `large` | Qwen3.5-9B-Q4_K_M.gguf | 5,3 GiB | für GPUs mit 8 GB Speicher oder mehr |
| `large-iq2` | Qwen3.5-9B-UD-IQ2_M.gguf | 3,4 GiB | 2 Bit, nur auf Wunsch; spürbar ungenauer |
| `granite` | granite-4.2-3b-Q4_K_M.gguf | 2,1 GiB | IBM Granite 4.2 3B, andere Modellfamilie, nur auf Wunsch |

Jeder Download hat eine festgelegte Größe und sha256 und wird geprüft, bevor er entpackt oder geladen wird; Einträge
ohne beides werden abgelehnt. Downloads werden nach einer Unterbrechung fortgesetzt, und ein vorgebautes Archiv wird
atomar neben dem vorherigen installiert.

### Welcher Build

Der selbst gebaute Server: Das Bau-Rezept wählt das GPU-Backend aus dem Hardwareprofil des Rechners und folgt dabei
seinem Betriebssystem, seiner Architektur und seiner C-Bibliothek (`recipe.choose_backend`). Der CPU-Build wird immer
zuerst gebaut und bleibt der Ersatz. Eine GPU, auf der Vulkan die CPU schlägt (jede eigene GPU, AMD-Radeon-GPUs und
-APUs wie der Ryzen Z1 Extreme, Intel Arc), bekommt einen Vulkan-Build; Mali-GPUs auf Boards erst, wenn `llama-bench`
zeigt, dass Vulkan schneller ist. NVIDIA-GPUs bekommen einen CUDA-Build, der zu jeder GPU und zum Treiber passt,
sobald die Bereitstellung des CUDA-Toolkits kommt; bis dahin läuft dort der CPU-Build, `runtime status` sagt das, und
`--backend vulkan` baut einen Vulkan-Server, der auch NVIDIA-GPUs nutzt. Modellstufe und Kontext folgen dann dem
Build, der tatsächlich läuft.

Die vorgebaute Wahl (`--prebuilt`): `llm-cli ditto runtime status` zeigt, welches Archiv sie installieren würde und
warum:

1. Archive, deren C-Bibliothek nicht zum Rechner passt, werden nie angeboten (musl, eine unbekannte C-Bibliothek
   oder eine ältere glibc als die des Archivs).
2. Ein ausdrückliches `--backend` hat Vorrang (ROCm wird nur so genutzt; unter Linux braucht es eine installierte
   HIP-Laufzeit und eine unterstützte GPU).
3. Apple Silicon bekommt den Metal-Build.
4. Eine NVIDIA-GPU bekommt den neuesten CUDA-Build, der zu jeder NVIDIA-GPU im Rechner und zu ihrem Treiber passt.
   GPUs, für die der Build nur PTX-Code enthält, brauchen den Treiber des jeweiligen Toolkits: Eine RTX 2080 Ti
   (Compute Capability 7.5) braucht zum Beispiel unter Linux Treiber 570.26 für den CUDA-12.8-Build und den Zweig
   R615 für CUDA 13.4 (unter Windows 551.61 für CUDA 12.4). Mit einem älteren Treiber wird die nächste Möglichkeit
   genommen, und der Grund sagt, dass ein neuerer Treiber CUDA ermöglicht.
5. Vulkan, wenn ein Vulkan-Treiber gefunden wird und sich die GPU lohnt: jede eigene GPU, AMD-Radeon-GPUs und -APUs
   (zum Beispiel der Ryzen Z1 Extreme) und Intel Arc. Ältere Intel-Grafik ist meist langsamer als die CPU und wird
   nur mit `--backend vulkan` genutzt.
6. Sonst der CPU-Build.

### Welches Modell

Es gewinnt die erste Regel, deren Bedingungen zutreffen und deren Modell passt:

| Regel | Stufe |
|---|---|
| GPU-Speicher von mindestens 7,5 GiB (eine vom Build genutzte eigene GPU oder etwa zwei Drittel des Speichers von Apple Silicon) | `large` |
| RAM bis 3,0 GiB | `small-q2` |
| RAM bis 4,5 GiB | `small` |
| sonst | `default` |
| Ersatz, wenn das Modell darüber nicht passt | `small`, dann `small-q2` |

„Passt“ heißt: Die Modelldatei plus 5 % plus der Key-Value-Cache für 4096 Tokens müssen in den RAM abzüglich 1,5 GiB
für das System passen, plus 95 % des Speichers einer eigenen GPU. Integrierte AMD- und Intel-GPUs teilen sich den RAM,
deshalb zählt bei ihnen nur der RAM. `runtime start --model <Stufe>` überstimmt die Wahl und merkt sie sich;
`--model auto` kehrt zur Empfehlung zurück.

### Wie der Server läuft

- **Nur geprüfte Builds:** `runtime start` startet den aktiven selbst gebauten (oder importierten) Build nur, wenn er
  seine Prüfung auf diesem Rechner bestanden hat, jede Datei noch zu seinem Manifest passt, er vom festgelegten
  Commit ist und für diesen Rechner gebaut wurde (Betriebssystem und Architektur, und bei einem `-march=native`-Build
  eine CPU desselben Herstellers mit jeder CPU-Funktion der CPU, auf der er gebaut wurde); sonst den
  CPU-Ersatz-Build, und sonst nichts, mit dem Grund. Das vorgebaute Archiv wird nur nach `install --prebuilt`
  gestartet. Ein Archiv, das eine frühere Version ohne diese Bitte heruntergeladen hat, wird nicht genutzt:
  `runtime status` nennt es, und `install --prebuilt` wechselt darauf, ohne es neu herunterzuladen. Ein selbst
  gebauter Build, der nicht genutzt werden kann, wechselt nie von selbst darauf.
- **Eine Startregel:** Die verbotenen Optionen, die geleerte Umgebung, Loopback, die Schlüsseldatei und die festen
  Server-Optionen kommen aus einem Modul, `orchestrator/runtime/server_policy.py`, das jeder Starter nutzt: Dittos
  Server, die Prüfung nach dem Build, die Perplexity-Prüfung und auf einem Board das Startskript, die systemd-Unit und
  die Prüfungen dort. Die Server-Zeile des Startskripts wird mit derselben Prüfung gehalten wie Dittos Befehlszeile,
  und die `--tools`-Prüfung des Servers ist hier und auf einem Board derselbe Befehl.
- **Kontextlänge:** aus dem freien Speicher neben dem Modell berechnet (freier GPU-Speicher, oder freier RAM bei
  CPUs, Apple Silicon und integrierten GPUs), auf 256 Tokens gerundet, mindestens 4096 und höchstens 32768 (`--ctx`
  legt sie fest).
- **Abschottung:** `llama-server` lauscht nur auf 127.0.0.1, auf einem freien Port. Ein neuer, zufälliger
  API-Schlüssel pro Start erreicht ihn nur über eine Schlüsseldatei, die nur du lesen kannst, und das Framework
  prüft, dass der Server Anfragen ohne Schlüssel ablehnt. Weboberfläche und Slots-Endpunkt sind aus, und der Server
  läuft mit `--offline`. Optionen, die dem Modell eigene Tools, einen Agentenmodus, MCP-Server oder entferntes
  Rechnen geben würden, die etwas herunterladen (Hugging Face, URLs, Docker Hub), die Prompts auf die Platte schreiben, Programme
  aus einem Ordner starten oder einen Schlüssel oder Token auf die Befehlszeile bringen, werden abgelehnt, in jeder
  Schreibweise, die llama.cpp annimmt (seine Kurzformen, und `--a_b` für `--a-b`). Der Server startet mit geleerter
  Umgebung: Außer deinem Benutzerordner, einem festen `PATH` und der C-Locale bekommt er nur die Variablen, die GPUs
  auswählen oder GPU-Treiber finden (`CUDA_VISIBLE_DEVICES`, `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`,
  `GGML_VK_VISIBLE_DEVICES`, `HSA_OVERRIDE_GFX_VERSION`, die Vulkan-Treibervariablen, `LD_LIBRARY_PATH` und wenige
  mehr), also erreicht ihn keine `LLAMA_*`-Option, keine `GGML_BACKEND_PATH`-Bibliothek, kein Proxy und kein
  Schlüssel.
- **GPU-Auslagerung:** Mit einem GPU-Build kommen so viele Schichten auf die GPU, wie hineinpassen.
- **Leerlauf:** Das Modell wird nach 600 Sekunden ohne Anfrage entladen und bei der nächsten Anfrage wieder geladen
  (`--sleep-idle`).
- **Zustand und Log:** `~/.llm-framework/runtime/llama.cpp/` enthält die Builds und den Serverzustand, dem nur
  vertraut wird, solange genau dieser Prozess (Prozess-ID, Startzeit, Programm) lebt. Die Schlüsseldatei wird beim
  Stoppen gelöscht. Das Log `~/.llm-framework/logs/llama-server.log` wird bei 5 MiB rotiert; 3 alte Logs bleiben.
- **Nachdenken:** Qwen3.5 denkt auf `llama-server` sonst in voller Länge nach, was auf einer CPU Minuten dauert.
  Deshalb schaltet der Aufwand `low` das Nachdenken aus; `medium` und höher behalten es.

### Wie die Laufzeit gebaut wird (selbst gebautes llama.cpp)

`llm-cli ditto runtime install` baut llama.cpp auf diesem Rechner aus der festgelegten Quelle: Es untersucht den
Rechner, erkennt die Toolchain, plant den CPU- und den GPU-Build, zeigt sie und fragt, holt und prüft die Quelle
einmal, baut und prüft den CPU-Build und macht ihn aktiv, dann dasselbe für den GPU-Build. Ein Build eines anderen
Rechners derselben Art lässt sich importieren (`ditto runtime export` dort, `ditto runtime import` hier). Der
offizielle vorgebaute Download bleibt nur als ausdrückliche Wahl `--prebuilt`, angezeigt als „prebuilt, not
self-built“.

- **Ein Rezept:** Aus dem Hardwareprofil des Rechners, der die Programme ausführen wird, wird ein Bauplan: der
  festgelegte Commit, jede Option von llama.cpp, die etwas herunterladen kann, aus, der Server ohne
  Unterprozess-Unterstützung (`LLAMA_SUBPROCESS=OFF`), `-march=native` auf diesem Rechner selbst oder die
  CPU-Merkmale der Probe für einen anderen Rechner. Ein Linux-Build mit Clang nutzt OpenMP nur, wenn libomp
  installiert ist. Alle CPU-Varianten für ein ARM-Board, dessen Merkmale die Probe nicht erkennen konnte, brauchen
  GCC 14 oder Clang 18 (die Liste von upstream enthält `armv9.2-a+sme`); mit einem älteren Compiler verlangt der
  Bauplan stattdessen eine neue Probe.
- **Werkzeuge:** Das Framework sucht Compiler, CMake (3.15 oder neuer) und Ninja und nennt, was fehlt; es installiert
  nie Werkzeuge und akzeptiert nie eine Lizenz für dich. Die parallelen Jobs richten sich nach dem Speicher: `-j` ist
  die Zahl der CPUs, aber höchstens der verfügbare RAM geteilt durch eine Schätzung pro Job (1 GiB für einen
  CPU-Build; `build_jobs` im Katalog).
- **Wo gebaut wird:** nativ unter Linux, in einer geleerten Umgebung, oder im Builder-Container des Frameworks. Dieses
  Image wird auf deinem Rechner aus `orchestrator/runtime/build/builder.Dockerfile` und einer per Digest festgelegten
  Debian-, Ubuntu- oder Alpine-Basis gebaut, deren C-Bibliothek nicht neuer ist als die des Rechners, der die
  Programme ausführt. Konfigurieren und Bauen laufen in Containern dieses Images ohne Netz, mit schreibgeschützt
  eingehängter Quelle, ohne Capabilities, mit schreibgeschütztem Wurzeldateisystem und deinem Benutzer, so wie die
  Konvertierungsschritte laufen. Beschreibbar sind nur die eigenen Ordner des Builds, und wenn das Framework danach
  die Programme einsammelt oder aufräumt, folgt es nie einem Link aus ihnen hinaus. Das Framework teilt dieses Rezept,
  nie ein fertig gebautes Image.
- **Keine Downloads:** Neben den Optionen wird die Ausgabe des Konfigurierens geprüft: Eine Zeile, die sagt, dass
  llama.cpp etwas geholt oder heruntergeladen hat, lässt den Build scheitern. Ebenso ein Konfigurieren, das für einen
  mit OpenMP geplanten Build keine OpenMP-Laufzeit gefunden hat (ggml würde nur warnen und ohne OpenMP bauen, sodass
  die Build-ID nicht mehr stimmt).
- **Prüfungen vor der Nutzung:** Jedes Programm muss eine ELF-Datei für die geplante Architektur sein, deren
  Bibliotheken im Build liegen oder Systembibliotheken sind; Geräte-Builds dürfen keine neuere glibc-Symbolversion
  brauchen, als das Gerät hat, und keine geteilte C++-Laufzeit. `llama-bench --version` und `llama-server --version`
  müssen den festgelegten Build und Commit melden, `llama-bench --list-devices` muss die geplante GPU zeigen, und
  `llama-server --tools all` muss abgelehnt werden. Ein Rauchtest und ein kurzer `llama-bench`-Lauf auf einem
  winzigen festgelegten Modell folgen, sobald der Katalog eines festlegt; bis dahin werden sie mit einem Hinweis
  übersprungen. Ein anderes Modell wird dort nie geladen. Programme werden nur aus dem eigenen Ordner des Builds
  genommen, nie aus `PATH`, und laufen mit geleerter Umgebung.
- **Ablage:** `~/.llm-framework/runtime/llama.cpp/builds/<Build-ID>/` enthält die Programme mit `build.json` (der
  Bauplan) und `manifest.json` (sha256 jeder Datei, Dauer, Bau-Rechner, Prüfergebnisse). Die Build-ID ist der Hash
  von allem, was die Programme verändert, also wird ein abgelegter Build wiederverwendet und nie neu gestempelt. Nur
  ein auf diesem Rechner geprüfter Build kann der aktive werden; die Aufbewahrung behält den aktiven Build, den
  CPU-Ersatz, den Build, aus dem ein laufender Server gestartet wurde, und die zwei neuesten anderen. Ein Build von
  einem deiner anderen Rechner kann importiert werden: Jede Datei wird gegen sein Manifest geprüft, die Build-ID
  gegen seinen Bauplan und der Commit gegen den Pin, du bestätigst, bevor eines seiner Programme läuft, und danach
  wird er wie ein lokaler Build geprüft. Die Version, die er melden muss, kommt aus dem Pin, nicht aus dem Archiv,
  und jedes Programm darin wird geprüft, auch eines, das das Archiv nicht aufführt. Dittos Import
  (`ditto runtime import`) lehnt einen `-march=native`-Build ab, der auf einer CPU eines anderen Herstellers oder mit
  einer CPU-Funktion gebaut wurde, die dieser CPU fehlt (etwa AVX-512), bevor irgendetwas läuft: Ein solcher Build
  bräche bei der ersten Rechnung mit SIGILL ab, und das erreichen die Prüfungen nicht, solange kein Smoke-Test-Modell
  festgelegt ist.
- **Gemessen** auf einer virtuellen x86-64-Maschine mit 4 Kernen (GCC 13.3, `-j4`): `llama-server` und `llama-bench`
  am Pin in 189 Sekunden samt Prüfungen, 28 MB Programme; `ditto runtime install` mit dem CPU-Build für Ditto
  (`llama-server`, `llama-cli`, `llama-bench`, `llama-perplexity`) in 195 Sekunden samt Prüfungen.

### Perplexity-Prüfung umgewandelter Modelle

Der Dataset-Manager (`orchestrator/Core/dataset_manager.py`) misst die Perplexity eines GGUF-Modells auf einer
Textdatei mit dem selbst gebauten `llama-perplexity` dieses Rechners, das `ditto runtime install` zusammen mit dem
Server baut. Es wird kein vorgebautes Laufzeit-Image geholt und kein `llama-perplexity` aus dem `PATH` genutzt: nur
ein gespeicherter Build, der seine Prüfung hier bestanden hat, noch zu seinem Manifest passt und vom festgelegten
Commit ist.

- **Katalogmodelle** (über sha256 festgelegt) laufen nativ.
- **Jedes andere Modell** (deine Umwandlungen, Downloads) gilt als nicht vertrauenswürdig. Es läuft nativ, ohne
  Docker-Aufwand, nur nach deiner ausdrücklichen Zustimmung, und diese Zustimmung wird vor dem Lauf ins
  Tool-Audit-Log geschrieben (`llm-cli tools audit`); ohne Zustimmung, oder wenn das Audit-Log nicht geschrieben
  werden kann, läuft nichts.
- **Docker-Sandbox (auf Wunsch):** Der Lauf geschieht im eigenen Builder-Image des Builds (nur ein im
  Builder-Container gemachter Build hat eines; es wird lokal gebaut und nie geholt), ohne Netzwerk, ohne
  Capabilities, mit schreibgeschütztem Wurzeldateisystem und deinem Benutzer; Programm, Modell und Text werden
  schreibgeschützt eingebunden.
- Jeder Lauf nutzt die gemeinsame Startregel (keine verbotene Option, eine geleerte Umgebung). Fehler sind Fehler mit
  Grund (zum Beispiel ein Text, der für den Kontext zu kurz ist), nie Platzhalterwerte.
- In dieser Version ruft noch nichts in GUI oder CLI die Prüfung auf; die Benchmark- und Auto-Tune-Arbeit der
  Roadmap wird das tun.

---

## Wissensbasis (RAG)

Die Wissensbasis gibt Ditto und MCP-Clients Dokumentation zum Durchsuchen (Einstellung `enable_rag_knowledge`, GUI:
**Enable Local Knowledge Base**). Bis zur ersten Nutzung wird nichts geladen.

- **Speicher:** Qdrant läuft eingebettet im Framework-Prozess, mit seinen Daten in `~/.llm-framework/qdrant` (kein
  Docker, kein offener Port). Der Speicher ist während der Nutzung gesperrt und wird nach 30 Sekunden ohne Zugriff
  freigegeben, sodass sich GUI und MCP-Server abwechseln können. Wer ihn aus mehreren Prozessen gleichzeitig nutzen
  will, setzt `qdrant_url` auf einen Qdrant-Server; ein entfernter Server braucht https und den API-Schlüssel als
  Geheimnis `qdrant_api_key` (nie Zugangsdaten in der URL).
- **Embeddings:** lokal mit FastEmbed auf ONNX Runtime berechnet; das Modell wird einmal nach `models/embeddings/`
  geladen. Mit `rag_embedding_model: auto` (Standard) behält eine vorhandene Wissensbasis das Modell, mit dem sie
  gebaut wurde; eine neue bekommt `intfloat/multilingual-e5-small` (384 Dimensionen, etwa 100 Sprachen, schnell auf
  einer CPU) oder `BAAI/bge-m3` (1024 Dimensionen), wenn ONNX Runtime CUDA hat und der Rechner mindestens 8 GB RAM.
  Jedes Modell bekommt eine eigene Sammlung; ein Modellwechsel vermischt oder verliert also nie Vektoren. Im
  Offline-Modus wird nichts heruntergeladen, und ein fehlendes Modell wird klar gemeldet.
- **Aufnehmen:** Seiten werden in Markdown umgewandelt und entlang ihrer Struktur geteilt (Überschriften, Absätze,
  Listen, Codeblöcke, Tabellen), bemessen in Tokens des Modells. Jeder Abschnitt wird mit Quelle, Titel und Kapitel
  gespeichert. Wird eine Seite erneut aufgenommen, wird sie an Ort und Stelle aktualisiert, und Abschnitte, die die
  neue Fassung nicht mehr hat, werden entfernt. Codeblöcke und Tabellen bleiben ganz, außer sie sind größer als der
  Kontext des Modells ([Große Codeblöcke und Tabellen](#große-codeblöcke-und-tabellen)).
- **Schnappschüsse:** Vor jedem Crawl wird die Wissensbasis als Schnappschuss in
  `~/.llm-framework/knowledge_snapshots` gesichert (die neuesten 5 bleiben). Einen Schnappschuss wiederherstellen
  kann man über GUI oder CLI noch nicht.
- **Suche:** hybrid. Dichte Vektoren finden die Bedeutung, BM25 findet exakte Begriffe wie Fehlercodes und
  Compiler-Flags; beide Ranglisten werden mit Reciprocal Rank Fusion verbunden. Derselbe Text aus mehreren Quellen
  kommt nur einmal. Danach kann ein Cross-Encoder die Ergebnisse neu bewerten ([Reranking](#reranking)).
- **Nutzung:** das Tool `search_knowledge` (Ditto, MCP-Clients); ohne Tools hängt Ditto passende Auszüge selbst an
  die Frage. Auch Healing-Analysen, Modulvorschläge und Build-Empfehlungen (Builder: **Use RAG Knowledge Base**)
  bekommen Auszüge. Ergebnisse gelten immer als nicht vertrauenswürdige Daten.
- **Dokumentation aufnehmen:** [Deep Ingest](#deep-ingest) im Modul-Assistenten und
  [Wissens-Packs](#community-zoo) aus dem Community-Zoo.

### Reranking

Die hybride Suche ordnet nach Wörtern und Bedeutung. Ob ein Auszug die Frage beantwortet, erkennt sie nicht, und sie
liefert immer etwas. Ein Reranker liest die Frage zusammen mit jedem Kandidaten und gibt ihm eine Relevanz zwischen
0 und 1. Kandidaten unter einer Relevanzschwelle fallen weg; Ditto bekommt dann lieber keinen Auszug als einen
unpassenden.

- **Modell** (Einstellung `rag_reranker`, Standard `auto`): ein lokaler Cross-Encoder auf FastEmbed/ONNX, der bei
  der ersten Nutzung nach `models/embeddings/` heruntergeladen wird, im Offline-Modus nie. Mit `auto`:

  | Rechner | Reranker |
  |---|---|
  | ONNX Runtime mit CUDA und mindestens 16 GB RAM | `onnx-community/bge-reranker-v2-m3-ONNX` (mehrsprachig, etwa 0,6 GB Download) |
  | mindestens 6 GB RAM | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (mehrsprachiges MiniLM, etwa 120 MB Download, etwa 0,5 GB im Speicher) |
  | weniger als 6 GB RAM | keiner |

  `off` schaltet das Reranking aus. Eine Modell-ID wählt einen anderen Cross-Encoder, den FastEmbed aufführt.
- **Relevanzschwelle** (Einstellung `rag_rerank_min_score`, 0 bis 1, standardmäßig nicht gesetzt): Ohne Wert gilt
  die für das Modell kalibrierte Schwelle, 0,2 für beide Modelle oben; 0 schaltet die Schwelle ab. In der
  Retrieval-Evaluation bekamen passende Auszüge 0,80 oder mehr und fachfremde 0,009 oder weniger.
- **Welche Suchen neu bewertet werden:** standardmäßig nur Build-Empfehlungen. Die Einstellung
  `rag_rerank_default: true` bewertet auch den Chat und das MCP-Tool `search_knowledge` neu. Healing-Analysen und
  Modulvorschläge werden nie neu bewertet.
- **Warum nur Build-Empfehlungen:** gemessen mit der [Retrieval-Evaluation](#retrieval-evaluation) auf einer
  geteilten CPU mit 4 Kernen. An den 34 Fällen, die keine `log`- oder `callsite`-Suchen sind, hob Reranking hit@1 von
  87 % auf 93 % und MRR von 0,93 auf 0,97, und alle 4 fachfremden Fragen bekamen keinen Auszug. Es kostet etwa 1 s für
  12 Kandidaten und 2 bis 2,6 s für 20 und brachte weniger als die für den Chat verlangten 0,05 MRR; der Chat bleibt
  deshalb ohne. Bei den letzten Zeilen gescheiterter Builds fand es den passenden Auszug in 2 von 6 Fällen unter den
  ersten 3 (hybride Suche: 5 von 6) und warf ihn in einem Fall unter die Schwelle; beim Suchtext der Modulvorschläge
  warf es jeden Auszug weg. Die Analyse von Build-Logs und die Modulvorschläge behalten deshalb die Reihenfolge der
  hybriden Suche.
- **Wie eine Suche neu bewertet wird:** bis zu 20 Kandidaten (das Dreifache der gewünschten Anzahl, mindestens 12),
  von jedem Titel, Kapitel und die ersten 1500 Zeichen; die Werte werden nach Text zwischengespeichert. Ein Reranker,
  der nicht laufen kann (im Offline-Modus nicht heruntergeladen, unbekannte Modell-ID, ein Fehler), wird einmal
  protokolliert, und Suchen behalten die hybride Reihenfolge bis zum nächsten Start oder einer Änderung von
  `rag_reranker`.

### Budget für Auszüge

Wenn Ditto einer Anfrage selbst Auszüge aus der Wissensbasis beifügt (Chat ohne Tools, Healing-Analysen,
Modulvorschläge, Build-Empfehlungen), nehmen sie höchstens ein Drittel von `chat_context_limit` ein (Standard 4096
Tokens, also etwa 1365 Tokens, geschätzt mit drei Zeichen pro Token). Die besten Auszüge kommen zuerst. Einer, der
nicht mehr passt, wird übersprungen, sodass ein kleinerer späterer noch mitkommen kann. Ist schon der erste Auszug zu
lang, wird er an einer Zeile gekürzt und mit `[…]` markiert; ein Codeblock, der an der Schnittstelle offen ist, wird
geschlossen, damit der Rest des Prompts nicht als Code gelesen wird.

### Stichwortsuche für Bezeichner

Der Stichwortindex (BM25) kennt auch die Teile von Bezeichnern: `-DGGML_CUDA=ON` wird über `GGML_CUDA`, `GGML` oder
`CUDA` gefunden, `rknn_toolkit2` über `rknn toolkit2` und `rknnToolkitLite` über `rknn Toolkit Lite`. Ein
führendes `-D` fällt nur bei Definitionen wie `-DNAME=WERT` oder `-DNAME_MIT_UNTERSTRICH` weg (`-DEBUG` behält es),
und Teile aus einer oder zwei Ziffern werden ausgelassen (`x86_64` ergibt `x86`, nicht `64`). Nur der
Stichwortindex bekommt die zerlegten Formen; der gespeicherte Text und die Vektoren bleiben, wie sie sind. Suchen aus
einem oder zwei Wörtern nutzen den Stichwortindex, wenn sie einen Bezeichner enthalten (eine Ziffer, einen
Unterstrich, zwei Großbuchstaben hintereinander oder einen Punkt oder Bindestrich innerhalb eines Worts, etwa
`E2041`, `GGML_CUDA`, `RKNN` oder `librkllmrt.so`); andere kurze Suchen nutzen nur die Bedeutung. In der
Retrieval-Evaluation hob das hit@3 der Bezeichner-Fragen von 83 % auf 100 %; die übrigen Ergebnisse blieben gleich.

Ein Dokument behält die Stichwortvektoren, mit denen es aufgenommen wurde, und bleibt auffindbar; nimmst du es
erneut auf (zum Beispiel mit einem neuen Deep Ingest derselben URL), gilt die aktuelle Zerlegung.

### Große Codeblöcke und Tabellen

Codeblöcke und Tabellen bleiben ganz, solange das Embedding-Modell sie ganz sieht: bis zu seinem Kontext abzüglich
96 Tokens für die Zeile mit Quelle, Titel und Kapitel, die mit jedem Abschnitt eingebettet wird (512 Tokens beim
Standardmodell `intfloat/multilingual-e5-small`, also 416 Tokens).
Größere werden an Zeilengrenzen in Stücke der normalen Abschnittsgröße geteilt (384 Tokens beim Standardmodell).
Jedes Stück wiederholt den Tabellenkopf oder öffnet den Codeblock neu, sodass auch die späteren Teile einer langen
Tabelle oder eines Skripts überhaupt gefunden werden (in der Retrieval-Evaluation, Antworten tief in solchen Blöcken:
hit@1 von 50 % auf 100 %). Dokumente in der Wissensbasis behalten ihre alten Abschnitte: Nimm sie erneut auf (zum
Beispiel mit einem neuen Deep Ingest derselben URL), damit es wirkt.

### Modelle im Speicher

Die Embedding-Modelle und der Reranker werden nach `rag_model_idle_minutes` (Standard 10) ohne Nutzung entladen und
bei der nächsten Suche wieder geladen, was einige Sekunden dauert. Die Zeit zählt ab dem Ende der letzten Suche oder
Aufnahme, nie währenddessen. `0` hält sie geladen; eine längere Zeit als 7 Tage (10080 Minuten) gilt als 7 Tage.
Der eingebettete Speicher wird davon getrennt nach 30 Sekunden ohne Zugriff freigegeben (siehe **Speicher** oben).

---

## Deep Ingest

Deep Ingest liest Dokumentationsseiten aus dem Web in die Wissensbasis ein. Du startest es mit **Deep Ingest (Add
Docs)** im Modul-Assistenten (**Tools > Create Hardware Module...**); die Wissensbasis muss eingeschaltet sein. Einen
CLI-Befehl dafür gibt es in dieser Version nicht.

Text- und Data-Mining öffentlich zugänglicher Inhalte ist erlaubt, solange der Rechteinhaber es sich nicht
maschinenlesbar vorbehalten hat (EU-Richtlinie 2019/790, Art. 4; § 44b UrhG). Deshalb prüft der Crawler diese
Signale selbst, statt dich etwas bestätigen zu lassen, und keine dieser Prüfungen lässt sich abschalten. Die frühere
Option *Respect robots.txt* und die Einstellung `crawler_respect_robots` gibt es nicht mehr.

### Der Hinweis vor dem ersten Crawl

- Vor dem ersten Deep Ingest jedes Benutzers erklärt ein Hinweis in der Sprache der Oberfläche, was gelesen wird (nur
  öffentlich erreichbare Seiten ohne Anmeldung, ausgehend von den Adressen, die du eingibst, innerhalb der Seiten-
  und Tiefengrenzen), welche Opt-outs beachtet werden, dass das Wissen auf diesem Rechner bleibt (wenn Ditto
  antwortet, gehen passende Auszüge an den gewählten KI-Anbieter; mit einem lokalen Anbieter verlässt nichts den
  Rechner) und dass Teilen in einem Target oder einem Community-Pack Quelle und Lizenz jeder Seite behält und deine
  ausdrückliche Freigabe braucht.
- Er ist eine Information, keine rechtliche Bestätigung: eine Schaltfläche, **Verstanden** (englische Oberfläche:
  *Understood*, auch Esc), nichts zum Ankreuzen.
- Er erscheint einmal pro Benutzer und erneut, wenn sich sein Text in einer Sprache ändert. Die Zustandsdatei
  `crawl_notice.json` im persönlichen Ordner (`~/.llm-framework`) speichert einen Hash seiner Texte. Lässt sich
  dieser Ordner nicht nutzen, erscheint der Hinweis jedes Mal.

### Was der Crawler beachtet

| Signal | Wo es gelesen wird | Wirkung |
|---|---|---|
| robots.txt (RFC 9309) | `/robots.txt` jeder Site, einmal pro Schema, Host und Port | Gesperrte Pfade werden nicht abgerufen. Es gelten die Gruppen, deren `User-agent` `LLM-Conversion-Framework-Bot` lautet (gleich in welcher Schreibweise), sonst alle `*`-Gruppen (eine Gruppe für `bot` gehört einem anderen Crawler); Muster mit `*` und `$` wirken; die längste Regel gewinnt, bei Gleichstand `Allow`. Eine robots.txt, die nicht erreichbar ist oder mit einem Serverfehler, 401 oder 403 antwortet, gilt als „alles gesperrt“ (bei 401 und 403 strenger als RFC 9309, das dann Crawlen erlaubt: eine verschlossene robots.txt kann einen Vorbehalt enthalten). |
| Header `X-Robots-Tag` | jede Antwort | `noindex`, `none`, `noai` und `noimageai` halten die Seite heraus; bei `nofollow` und `none` werden ihre Links nicht verfolgt. Direktiven für diesen Crawler (`LLM-Conversion-Framework-Bot: noai`) zählen, solche für andere Crawler nicht. |
| `<meta name="robots">` und `<meta name="llm-conversion-framework-bot">` | HTML-Seiten | dieselben Direktiven wie beim Header |
| TDM Reservation Protocol (W3C TDMRep 1.0) | `/.well-known/tdmrep.json` pro Site (die erste passende Regel entscheidet), der Header `tdm-reservation`, `<meta name="tdm-reservation">`, `tdm:reservation` in den PDF-Metadaten | Ein Vorbehalt hält die Seite heraus; eine `tdm-policy` wird im Log genannt. |

- Ein Vorbehalt aus irgendeiner dieser Quellen gewinnt. TDMRep erlaubt einer späteren Quelle, einen früheren
  Vorbehalt aufzuheben; der Crawler bleibt auf der vorsichtigen Seite.
- Weiterleitungen werden nicht direkt verfolgt. Ihre Ziele kommen in die Warteschlange und werden wie jede andere
  Adresse geprüft: Sicherheitsprüfung, robots.txt und `tdmrep.json` ihres eigenen Hosts. Ein Link, der auf einen
  anderen Host weiterleitet, wird übersprungen; nur eine Weiterleitung der Start-URL (zum Beispiel von `example.com`
  auf `www.example.com`) verlegt den Crawl auf diesen Host. Höchstens 5 Weiterleitungen pro Adresse.
- Der Crawler nennt sich `LLM-Conversion-Framework-Bot/<Version> (Open Source;
  +https://github.com/Smilez1985/llm_conversion_framework)`, mit der echten Version des Frameworks. Betreiber von
  Websites können ihn unter diesem Namen in der robots.txt und in einem Meta-Tag ansprechen.

### Netzwerkschutz und Grenzen

- Private, Loopback- und andere nicht öffentliche Adressen sind gesperrt (SSRF-Schutz), und jede Verbindung wird an
  die geprüfte Adresse gebunden, damit eine DNS-Antwort nicht zwischendurch ausgetauscht werden kann.
- Der Deep-Ingest-Dialog legt für jeden Crawl die Link-Tiefe (0 bis 5; 0 liest nur die eingegebenen Seiten), das
  Lesen von PDFs und die Pause zwischen Anfragen (0 bis 10 s, auch Bruchteile einer Sekunde) fest. Er öffnet sich
  mit den Einstellungen `crawler_max_depth` (Standard 2), `crawler_download_pdfs` (Standard an) und
  `crawler_rate_limit` (Standard 1 s); eine größere eingestellte Tiefe oder Pause bleibt und hebt die Obergrenze an.
  Was du im Dialog änderst, gilt nur für diesen Crawl.
- Aus den Einstellungen: höchstens `crawler_max_pages` Seiten pro eingegebener URL (Standard 50) und nur Seiten in
  den Sprachen aus `crawler_languages` (Standard Englisch und Deutsch; eine leere Liste behält alle). Ein Crawl hört
  außerdem nach fünfmal so vielen Anfragen auf, wie seine Seitengrenze erlaubt (Weiterleitungen und gescheiterte
  Abrufe zählen mit).
- Jede Antwort wird bis zu einer Grenze gelesen (HTML-Seiten 8 MiB, PDFs 64 MiB, robots.txt 500 KiB,
  `tdmrep.json` 1 MiB) und muss innerhalb von 60 s vollständig ankommen (PDFs 180 s), sodass eine Site mit einer
  endlosen oder tröpfelnden Antwort weder den Speicher füllen noch den Crawl festhalten kann. Eine größere Seite
  wird übersprungen, eine langsamere scheitert. Von einer größeren robots.txt zählen die ersten 500 KiB (RFC 9309
  erlaubt diese Grenze); eine robots.txt, die nicht rechtzeitig ankommt, bedeutet „alles verboten“, und eine
  größere `tdmrep.json` wird nicht verwendet.

### Fortschritt, Stopp und Ergebnis

- Der Assistent zeigt vor jeder Anfrage „Seite N von M: <Adresse>“ (M ist die Seitengrenze) mit einem
  Fortschrittsbalken.
- Seiten werden gespeichert, sobald sie gelesen sind. **Stoppen** beendet den Crawl nach der aktuellen Seite (eine
  laufende Pause zwischen Anfragen wird abgekürzt, ebenso eine Seite, die noch ankommt); was gespeichert ist, bleibt.
  Wer den Assistenten während eines Crawls schließt, stoppt ihn genauso: Das Fenster reagiert weiter und schließt
  sich, sobald die aktuelle Seite gespeichert ist.
- Das Ergebnis über alle eingegebenen URLs wird erst gemeldet, wenn der letzte Schreibvorgang zurückgekehrt ist:

  | Ergebnis | Wann | Meldungsfenster |
  |---|---|---|
  | Erfolg | jede eingegebene URL hat Seiten gespeichert; nichts ist gescheitert, nichts hat eine Site vorbehalten, der Crawl wurde nicht gestoppt | Information |
  | teilweise | Seiten sind gespeichert, aber andere sind gescheitert, wurden von der Site vorbehalten oder der Crawl wurde gestoppt | Warnung |
  | Fehlschlag | nichts wurde gespeichert | Fehler |

- Das Crawl-Protokoll im Assistenten (und das Log des Frameworks) nennt jede Seite, die nicht gespeichert wurde, mit
  ihrem Grund, zum Beispiel „disallowed by robots.txt“, „X-Robots-Tag: noai“, „TDM reservation (...)“, „redirect
  from ... leaves the crawled site“, „language 'fr' is not one of de, en“, „PDF downloads are switched off“,
  „neither HTML nor PDF (image/png)“, „no text“, „response larger than 8 MiB (the limit for HTML pages)“ oder „PDF
  support (pypdf) is not installed“, und jede Seite, die sich nicht abrufen oder lesen ließ (HTTP-Status, „no
  complete response within 60 s“, unlesbares PDF, zu viele Weiterleitungen). Jede Seite, die ankommt
  (HTTP 200), zählt zur Seitengrenze, ob gespeichert oder nicht. Vorbehalte von Sites und Fehler machen das
  Ergebnis zu „teilweise“, die übrigen Gründe nicht.

### Gesprochene Ansagen

- Der Hinweis („Bitte lesen Sie den Hinweis vor Ihrem ersten Deep Ingest.“), sobald er auf dem Bildschirm steht, und
  das Ergebnis eines Deep Ingest werden zusätzlich in der Sprache der Oberfläche gesprochen.
- Sprachausgaben, die erste, die funktioniert: unter Windows Windows PowerShell 5.1 mit System.Speech (aus dem
  Systemordner gestartet; der Text reist in einer Umgebungsvariablen, nie als Code); unter macOS `say` (mit einer
  Stimme der Oberflächensprache, wenn eine installiert ist); unter Linux `spd-say` (speech-dispatcher), sonst
  `espeak-ng` oder `espeak`. Dafür wird nichts installiert; ohne Sprachausgabe bleibt es still.
- Es steht nie im Weg: ein Hintergrund-Thread mit 30 s Zeitlimit, höchstens 300 Zeichen, keine Audiodateien, Mixer
  und Systemlautstärke werden nie angefasst, und ein Fehler hält nie etwas auf. Jede Ansage steht auch im Log, ist
  also nie nur hörbar.
- Still bleibt es, wenn die Einstellung `audio_announcements` aus ist (Menü **Tools > Gesprochene Ansagen**,
  englische Oberfläche: *Spoken Announcements*, ein Häkchen, das sofort gespeichert wird), wenn
  `LLM_FRAMEWORK_NO_AUDIO` gesetzt ist (auf alles außer leer, `0`, `false`, `no` oder `off`), unter CI (`CI`
  ebenso gesetzt), in einer SSH-Sitzung ohne Bildschirm und unter Linux ohne Bildschirm (kein
  `DISPLAY` oder `WAYLAND_DISPLAY`).

---

## MCP-Server

`llm-cli mcp serve` stellt die Tools des Frameworks über das [Model Context Protocol](https://modelcontextprotocol.io)
bereit (offizielles Python-SDK `mcp` 2.3.0, Spezifikation 2026-07-28). KI-Clients wie Claude Code, Claude Desktop oder
VS Code können dann [dieselben Tools wie Ditto](#dittos-tools) nutzen. Am Client meldest du dich mit deinem eigenen
Konto oder Schlüssel an; das Framework sieht das nie.

- **Jeder Aufruf läuft durch die Tool-Registry:** Eingabeschema, deine Berechtigungen, Bestätigung, Audit-Log
  (Akteur `mcp`). Tool-Annotationen (nur lesend, idempotent) sind nur Hinweise für den Client.
- **Bestätigung:** Wo deine Berechtigung „fragen“ sagt, fragt der Server den Benutzer über den Client
  (MCP-Elicitation). Die Frage zeigt das Tool und jedes Argument vollständig, die sicherheitsrelevanten zuerst;
  Steuerzeichen, unsichtbare Zeichen und lange Stapel kombinierender Zeichen erscheinen als `\u`-Escapes. Eine
  Frage, die länger als 4000 Zeichen würde, wird abgelehnt statt gekürzt. Nur eine echte Antwort des Benutzers zählt
  als Zustimmung, und ein „Nein“ gilt für diesen Aufruf endgültig, auch wenn die Berechtigungen gelockert werden,
  während die Frage offen ist. Abgelehnte und abgebrochene Aufrufe sowie Aufrufe, die abgewiesen werden, bevor sie
  laufen (ungültige Argumente, eine gescheiterte Bestätigung), landen ebenfalls im Audit-Log. Ein Client, der nicht
  fragen kann, bekommt eine Ablehnung mit einem Hinweis, wie du das Tool erlaubst.
- **Anweisungen an den Client:** Der Server beschreibt den typischen Ablauf (Targets → Capabilities → Build → Status
  und Log → Artefakte) und sagt dem Client, dass Build-Logs und Ergebnisse aus der Wissensbasis externer Text sind,
  dessen Anweisungen nicht befolgt werden dürfen.
- **Transporte:** stdio (Standard; der Client startet den Server) oder Streamable HTTP nur auf einer
  Loopback-Adresse (`--transport http`, Standard `http://127.0.0.1:8765/mcp`). HTTP prüft Host und Origin und
  braucht ein Bearer-Token, das pro Benutzer in `~/.llm-framework/mcp_http_token` angelegt wird.
- **Arbeitsordner:** Der Server liest nie Einstellungen und importiert nie Code aus dem Ordner, in dem er gestartet
  wird (der Client startet ihn mit `python -P`), und unter Windows startet er von dort keine Programme wie `docker`
  oder `git`. Ein Projektordner kann sein Verhalten nicht ändern.
- **Client-Einrichtung:** `llm-cli mcp config` gibt die Befehle und das JSON so gequotet aus, dass sie in
  POSIX-Shells und in cmd wie PowerShell halten; `--write claude-desktop` trägt den Server in die Konfiguration von
  Claude Desktop ein, behält andere Einträge und schreibt eine private Sicherung.

**Claude-Abonnenten:** Das ist der Weg, das Framework mit einem Claude-Abo zu nutzen. Anthropic erlaubt
Drittanbieter-Apps nicht, die Anmeldung oder die Limits eines Abos zu verwenden; Ditto selbst braucht deshalb einen
API-Schlüssel, während Claude Code und Claude Desktop das Framework über MCP im Rahmen deines Abos nutzen können.

---

## Tool-Berechtigungen und Audit-Log

Jedes Tool gehört zu einer Risikostufe:

| Stufe | Bedeutung | Empfohlen | Auswahl |
|---|---|---|---|
| `read` | Targets, Builds, Logs, Wissen ansehen | allow | allow, ask, deny |
| `local_write` | Auf diesem Rechner etwas ändern (Builds starten oder abbrechen, über Healing entscheiden) | ask | allow, ask, deny |
| `external` | Andere Geräte erreichen oder Daten löschen | ask | ask, deny; einzelne Tools lassen sich einzeln erlauben |
| `forbidden` | Nie aufrufbar (zum Beispiel beliebige Shell-Befehle) | deny | deny |

Tools der Stufen `external` und `forbidden` gibt es noch nicht; Übertragen und Hochladen stehen nicht als Tools zur
Verfügung.

- Die Berechtigungen werden einmal abgefragt (GUI-Dialog beim ersten Start, `llm-cli ditto setup`) und in
  `~/.llm-framework/tool_policy.json` gespeichert (nur für dich lesbar). Einzelne Tools lassen sich in der GUI
  abweichend einstellen.
- Bis zur Einrichtung sind nur lesende Tools erlaubt, alles andere fragt.
- Eine beschädigte oder unlesbare Berechtigungsdatei oder ein Link auf eine fehlende verbietet jedes Tool, statt auf
  lockerere Standardwerte zurückzufallen; eine beschädigte Datei bleibt als `tool_policy.json.bad` erhalten, und
  `llm-cli doctor`, `llm-cli tools list` und `llm-cli mcp serve` weisen darauf hin.

**Audit-Log** (`~/.llm-framework/audit.db`, SQLite): Jeder Tool-Aufruf wird mit Zeit, Akteur, Tool, Entscheidung, ob
ein Mensch bestätigt hat, und Ergebnis festgehalten. Argumente werden geschwärzt und gekürzt gespeichert, dazu ein
Hash des vollständigen Texts. Jeder Eintrag enthält den Hash des vorherigen; mit dem Schlüssel aus dem Schlüsselbund
deines Betriebssystems ist das eine HMAC-Kette, die jemand, der nur die Datenbank bearbeiten kann, nicht neu berechnen
kann. Nach jedem Eintrag wird der neueste zusätzlich in eine Anker-Datei geschrieben, sodass auch das Entfernen der
neuesten Einträge auffällt. `llm-cli tools audit --verify` prüft die Kette und unterscheidet einen geänderten Eintrag
von einem falschen Schlüssel. Jeder Eintrag wird in einer eigenen Transaktion geschrieben, damit mehrere Prozesse die
Kette nicht aufspalten. `llm-cli tools audit --archive` legt das Log und seinen Anker beiseite (mit Zeitstempel
aufbewahrt) und beginnt eine neue Kette, zum Beispiel nachdem `llm-cli doctor` ein Problem mit dem Log gemeldet hat.

---

## Geheimnisse und persönlicher Zustand

- **Geheimnisse** (API-Schlüssel, Tokens): verschlüsselt (Fernet, jeder Eintrag mit einer Schlüssel-ID) im Tresor
  `~/.llm-framework/secrets.store`. Standardmäßig liegt sein Schlüssel im Schlüsselbund deines Betriebssystems
  (Windows-Anmeldeinformationsverwaltung auf dem lokalen Rechner, macOS-Schlüsselbund, Secret Service unter Linux),
  ein Eintrag je Tresor, nie in einer Datei neben dem Speicher. Ein Schlüsselbund, der nicht innerhalb von 3 Sekunden
  antwortet, nichts behält oder im Klartext speichert, wird nicht verwendet; dann hält das Framework an und nennt die
  Möglichkeiten (die GUI zeigt eine Auswahl, die CLI endet mit Exit-Code 5 und nennt
  `llm-cli ditto auth storage --use ...`) für die Einstellung `secret_storage` (oder `LLM_SECRET_STORAGE` für einen
  einzelnen Lauf): `helper`
  (der Befehl in `secret_helper`, ohne Shell ausgeführt), `passphrase` (einmal je Start abgefragt), `insecure` (eine
  Schlüsseldatei neben dem Tresor, im Ruhezustand nicht verschlüsselt) oder `ephemeral` (nur im Arbeitsspeicher).
  Der Tresor wird atomar unter einer Sperre geschrieben, die GUI, CLI und MCP-Server teilen, ist nur für dich lesbar,
  liegt nie in einer Git-Arbeitskopie, und ein beschädigter Tresor wird nie überschrieben. `llm-cli doctor` warnt
  vor der Schlüsseldatei und vor einem Schlüsselbund, der Geheimnisse nicht schützt. `llm-cli ditto auth
  rotate-key` ersetzt den Schlüssel des Tresors und verschlüsselt jeden Eintrag neu.
- **Schwärzen:** Eine Tabelle der Secret-Formate (`orchestrator/security/secret_patterns.py`) teilen sich der
  Secret-Scan des Repositorys und die Schwärzung zur Laufzeit. Zugangsdaten (bekannte Schlüsselformate, Header mit
  Zugangsdaten, Tokens in URLs und Formulardaten, Zeilen wie `HF_TOKEN=...`) werden aus jeder Logzeile und jedem
  Traceback entfernt (aus den eigenen Logs des Frameworks und denen der SDKs und HTTP-Bibliotheken), aus Build-Logs,
  die über die Tools gelesen werden, aus Fehlermeldungen von Anbietern und Tools, aus den Argumenten im Audit-Log und
  aus allem, was an Cloud-Anbieter geht. Die Debug-Logs der SDKs bleiben aus, außer du startest mit `--debug-http`.
- **Persönlicher Ordner** `~/.llm-framework` (`LLM_FRAMEWORK_HOME`), nur für deinen Benutzer: Tool-Berechtigungen,
  Audit-Log, SSH-Schlüssel und `known_hosts`, MCP-Token, Chatverlauf, Geheimnisse, Wissensbasis und Schnappschüsse,
  lokale Laufzeit, der [Eingabeverlauf](#eingabeverlauf), der Zustand des
  [Hinweises vor Deep Ingest](#der-hinweis-vor-dem-ersten-crawl), der zwischengespeicherte
  [Community-Index](#der-signierte-index) mit den festgehaltenen Autorenschlüsseln und zwei Zufallswerte pro
  Installation (einer für die Datenschutz-Platzhalter, einer als anonyme Cache-ID für OpenAI). Deine Schlüsselpaare
  für [Community-Packs](#verschlüsselte-packs-zum-privaten-teilen) liegen im verschlüsselten Secrets-Speicher.
- **Einstellungen** werden nie aus dem Arbeitsverzeichnis gelesen: `LLM_FRAMEWORK_CONFIG_DIR`, dann `configs/` in der
  Installation, wenn du dort schreiben darfst, dann der persönliche Ordner.
- **Datenordner** (`output/`, `cache/`, `models/`) liegen in der Installation, wenn du dort schreiben darfst, sonst im
  persönlichen Ordner (unter Windows ist `C:\Program Files` für normale Benutzer schreibgeschützt). Sie entstehen,
  wenn ein Build oder ein Download sie zum ersten Mal braucht, nicht bei jedem Start.

---

## Eingabeverlauf

Die Eingabefelder der GUI bieten an, was du zuletzt verwendet hast, das Neueste zuerst.

| Feld | Wo | Vorausgefüllt | Gemerkt, wenn |
|---|---|---|---|
| Modell | Builder, **Source Model** | das zuletzt verwendete | ein Build abgeschickt wird |
| Kalibrierungsdatensatz | Builder, **Calibration** | nein, nur angeboten | ein Build mit Datensatz abgeschickt wird |
| Target und Board | Builder | das zuletzt gebaute Target und Board, die die Target-Definitionen noch anbieten; ein Target ohne festgelegte Boards bietet die letzten Boards als Vorschläge an | ein Build abgeschickt wird |
| Profil | Deployment | das vorher gewählte Profil, sonst das zuletzt verwendete | ein Deployment gestartet wird |
| Deep-Ingest-URL | Liste **Recent** im Deep-Ingest-Dialog, übernommen mit **Add** | nein, nur angeboten | der Dialog bestätigt wird |
| Gerät (IP-Adresse oder Hostname) | Deployment, entfernter Hardware-Scan (**Scan / Import...**) | das zuletzt verwendete | sein SSH-Host-Schlüssel bestätigt ist |

- Der Verlauf gilt pro Benutzer: `input_history.json` im persönlichen Ordner (`~/.llm-framework` oder
  `LLM_FRAMEWORK_HOME`), nur für dich lesbar, höchstens 20 Einträge pro Feld, ohne Doppelte. Er gehört nicht in die
  gemeinsame `configs/config.yml`; die frühere Einstellung `input_history` gibt es nicht mehr.
- Er enthält nie Geheimnisse. Nicht gemerkt werden Werte, die wie ein API-Schlüssel oder ein Token aussehen, URLs mit
  Benutzername oder Passwort, URLs mit einem Parameter für Zugangsdaten oder Sitzungen in der Abfrage, im Fragment
  oder als Pfadparameter (etwa `?token=`, `?api_key=`, `#access_token=`, `;jsessionid=` oder `?sid=`),
  `user:password@host`, Text mit Steuerzeichen und Werte mit mehr als 1000 Zeichen. Dieselben Regeln gelten für eine
  von Hand bearbeitete Datei.
- Eine beschädigte Datei beginnt einen leeren Verlauf; die alte Datei bleibt als `input_history.json.bad` erhalten.
- **Tools > Eingabeverlauf löschen...** (englische Oberfläche: *Clear Input History...*) fragt einmal nach, löscht
  dann die Datei und die `.bad`-Kopie, und die Felder werden sofort neu gefüllt.

---

## Hardware-Probe und neue Targets

- **Probe:** Eine Hardware-Probe in drei Umsetzungen schreibt dieselbe Datei, `target_hardware_config.txt`
  (Schema 2): `scripts/hardware_probe.py` (der Hauptweg: nur Standardbibliothek, Python 3.8 oder neuer, Linux, Windows
  und macOS) und die Ersatzwege `scripts/hardware_probe.sh` (jede POSIX-Shell unter Linux, für Boards ohne Python)
  und `scripts/hardware_probe.ps1` (Windows). Sie hält das Betriebssystem fest (ID, Version, Familie, Variante wie
  DietPi, Kernel, Init-System), die Architektur des Userlands, die C-Bibliothek und ihre Version (glibc, musl), die
  CPU (Modell, Kerne, ARM-Kerncluster, die x86- und ARM-Merkmale, für die ggml baut, etwa AVX-512 oder das ARM-
  Skalarprodukt), Speicher, SoC, NPU und jede GPU (NVIDIA Compute Capability, AMD gfx-Target, Vulkan); keinen
  Hostnamen, keine Seriennummer und keine MAC-Adresse. Die Probes brauchen kein Netz, fragen nie nach, installieren
  nichts und verlangen keine Administratorrechte, und externe Werkzeuge laufen mit Zeitlimit. Tests führen die `.py`
  und die `.sh` auf denselben Testrechnern aus und verlangen dasselbe Ergebnis. Das installierte Paket enthält eine
  byte-gleiche Kopie von `hardware_probe.py` (`orchestrator/runtime/hardware_probe.py`), also funktioniert die
  Hardware-Erkennung des Frameworks auch ohne den Ordner `scripts/`.
- **Wo sie läuft:** auf dem Rechner, für den gebaut wird. `llm-cli scan` untersucht diesen Rechner mit der
  Python-Probe und behält das Profil als `local`; `llm-cli scan --device` führt sie per SSH auf einem Board aus (die
  Python-Probe mit `python3 -I`, wenn das Board Python 3.8 oder neuer hat, sonst die Shell-Probe; auf dem Board wird
  nichts installiert) und behält das Profil unter der ID des Boards; der SSH-Benutzer wird zum Deploy-Benutzer des
  Boards (nie root). In der GUI importiert **Scan / Import...** eine Probe-Datei, untersucht ein Board per SSH mit
  denselben Regeln für das Vertrauen beim ersten Kontakt wie beim Deployment oder untersucht diesen PC; die
  Scan-Ergebnisse legt sie in `<cache_dir>/probes/` ab. Der SSH-Scan der GUI nutzt dieselbe entfernte Probe wie
  `llm-cli scan --device`: Das Skript kommt über den SSH-Kanal, also funktioniert er auch auf Boards, die `/tmp` mit
  `noexec` einhängen, und das Profil wird unter der ID des Boards abgelegt. Rechner ohne
  Netzzugang führen eine Probe von Hand aus ([README](../README_DE.md#die-hardware-des-boards-untersuchen)).
- **Dittos lokales Modell** nutzt dieselbe Probe: Die Hardware, auf der Build- und Modellwahl beruhen, kommt aus einem
  Probe-Profil, und freier RAM und freier VRAM werden bei jedem Start neu gelesen.
- **Neues Target per Fragen:** `llm-cli module create` oder der Assistent unter **Tools > Create Hardware
  Module...**.
- **Neues Target aus einer Probe:** `llm-cli module generate-ai <Probe-Datei>` oder die KI-Analyse im Assistenten.
  Ditto schlägt Architektur, SDK, Basis-Image, Pakete, CPU- und CMake-Flags, Setup-Befehle und den
  Quantisierungsblock von `build.sh` vor; du siehst zuerst den ganzen Vorschlag.
- **Die Geräte-Laufzeit im Modul:** Jede neue `target.yml` bekommt einen Block `runtime:`, den Plan des Build-Rezepts
  für die llama.cpp-Laufzeit des Boards aus der Probe des Targets (die im Assistenten eingelesene Probe-Datei, die
  Probe-Datei von `module generate-ai` oder `llm-cli module create --probe <Datei>`): Architektur, Betriebssystem, die
  C-Bibliothek, die der Cross-Build nicht überschreiten darf, die Builder-Basis, die CPU-Optionen (für den RK3566
  `armv8.2-a+dotprod+fp16`), die Backends und die Programme. Er sagt, dass llama.cpp aus dem Quelltext gebaut wird;
  er ist eine Aufzeichnung, und die Laufzeit wird immer neu aus der eigenen Probe des Geräts geplant. CPU- und
  CMake-Flags aus dem Assistenten oder von Ditto stehen dort unter `advisory` und werden nie angewendet.
- **llama.cpp in neuen Targets:** Das Vorlagen-Dockerfile baut die Konvertierungswerkzeuge wie das Rockchip-Image
  ([Konvertierungswerkzeuge abgestimmt auf den Build-Rechner](#konvertierungswerkzeuge-abgestimmt-auf-den-build-rechner)).
  Die GPU-Vorlage nimmt CUDA 12.9.1 aus den Redist-Archiven von NVIDIA (Manifest per sha256 festgelegt, jedes Archiv
  geprüft) auf Ubuntu 22.04, per Digest festgelegt; das vorgebaute Entwicklungs-Image von NVIDIA ist nur ein
  ausdrücklicher Opt-in (ein Basis-Image, das du selbst wählst; Dittos Vorschläge enthalten nie eines), per Digest
  festgelegt und als „not self-built“ gekennzeichnet.
- **Prüfungen, bevor etwas geschrieben wird:** Namen, Images, Pakete und Flags werden streng geprüft; Shell-Code wird
  gegen eine Sperrliste und mit `bash -n` geprüft; Dateien werden nur innerhalb von `targets/` geschrieben; ein
  vorhandenes Target wird nie überschrieben. Setup-Befehle laufen später als Benutzer ohne Sonderrechte und ohne
  Netzwerk. Ein Target, das ein vorgebautes llama.cpp-Image bezieht, llama.cpp-Programme aus einem anderen Image
  kopiert, Release-Archive oder Programme herunterlädt oder ein llama.cpp-Paket installiert, wird abgelehnt: Das
  Framework teilt Rezepte, keine Binärdateien.
- **Wissens-Schnappschuss (auf Wunsch):** Auf der letzten Seite des Assistenten schreibt **Attach the knowledge
  crawled for this target** (standardmäßig aus, erst nach einem Deep Ingest im Assistenten wählbar) die Seiten, die
  von den Deep-Ingest-URLs des Assistenten gecrawlt wurden, bereinigt nach `knowledge/initial_knowledge.json` im
  neuen Target, mit einer `README.md`, die sagt, was darin steht. Solange ein Deep Ingest noch läuft, bleibt die
  Schaltfläche *Finish* des Assistenten gesperrt (die Seite sagt, warum), damit der Schnappschuss jede gecrawlte
  Seite enthält; stammt keine gespeicherte Seite von diesen URLs, sagt der Assistent, dass kein Schnappschuss
  angehängt wurde. Der Rest deiner Wissensbasis kommt nie in ein Modul, und `llm-cli module create` und
  `module generate-ai` hängen nichts an. Das Framework nimmt einen solchen Schnappschuss nicht in eine Wissensbasis
  auf; zum Teilen von Wissen exportierst du ein Wissens-Pack.
- **Export für die Community:** Die letzte Seite des Assistenten bietet außerdem **Export for community after
  creating** an (siehe [Export für die Community](#export-für-die-community)).

Einzelheiten: [Module_Developer_Guide_DE.md](Module_Developer_Guide_DE.md).

---

## Gesundheitscheck (doctor)

`llm-cli doctor` (und das nur lesende Tool `run_doctor`) führt feste Prüfungen aus und erklärt jeden Befund:

| Bereich | Prüfungen |
|---|---|
| Python | unterstützte Version (3.11 bis 3.14) |
| Docker | Daemon erreichbar, buildx vorhanden |
| Speicherplatz | freier Platz für den Cache- und den Ausgabeordner (Warnung unter 20 GB, Fehler unter 5 GB) |
| Targets | Targets gefunden, Capabilities lesbar |
| Sicherheit | persönlicher Ordner privat, Secrets-Speicher nutzbar, Schlüsselbund schützt Geheimnisse |
| KI | Tool-Berechtigungen eingerichtet und lesbar, Audit-Log intakt (geändert, entfernt, falscher Schlüssel, fehlender Anker, kein Schlüssel) |
| Daten | mitgelieferte Kalibrierungsdaten intakt (Prüfsummen) |
| Community | installierte Community-Packs gegen den zwischengespeicherten Index: gesperrt (Fehler), seit der Installation verändert (Warnung), Vertrauensstufe `local` (Information) |
| Laufzeit | der genutzte selbst gebaute llama.cpp-Build: veränderte Dateien (Fehler), älter als der Pin (Warnung); das vorgefertigte Archiv, als nicht selbst gebaut gekennzeichnet, genutzt oder installiert, aber nicht genutzt (Information); ob dieser Rechner die Werkzeuge für einen nativen Build hat (Information) |

`--fix` wendet die automatischen Korrekturen bis `--tier` an (1: macht den persönlichen Ordner privat; 2: entfernt
zusätzlich verwaiste Framework-Images und den Docker-Build-Cache) und prüft danach erneut. Exit-Codes: 0 OK,
1 Warnungen, 2 Fehler; `--json` für Skripte.

---

## Community-Zoo

Der Community-Zoo teilt drei Arten von Packs: **Targets** (Hardware-Module), **Rezepte** (wie ein Modell für ein
Target gebaut wurde) und **Wissen** (Seiten für die Wissensbasis). Packs werden aus einem signierten Community-Index
aufgelistet, über einen Quarantäne-Ordner installiert und aus deiner eigenen Arbeit für einen Pull Request
exportiert. Befehle: `llm-cli zoo list|show|install|verify|export|keys`; in der GUI der
[Community Hub](#community-hub). Das Framework lädt nie etwas hoch und fragt nie nach einem Repository-Token, liest
oder zeigt keins.

**Stand heute:** Ein Community-Repository, ein Index oder ein Maintainer-Schlüssel ist noch nicht veröffentlicht.
`configs/community_keys.yml` führt keinen Schlüssel, und die Einstellungen `community_index_url` und `community_repo`
sind standardmäßig leer. Deshalb zeigen `llm-cli zoo list` und der Hub nur installierte Packs, installieren lassen
sich nur Pack-Dateien (Vertrauensstufe `local`), und ein Pull Request hat kein Repository, an das er gehen kann.

### Packs

- Ein Pack ist eine ZIP-Datei mit `pack.json` (das Manifest: Art, ID, Version, Autor, Lizenz, Mindestversion des
  Frameworks, Erstellungszeit sowie sha256, Größe und Dateimodus jeder Datei), einer optionalen `pack.sig` (die
  Ed25519-Signatur des Autors über das Manifest) und genau den aufgeführten Dateien. Beim Lesen wird nichts anderes
  angenommen: keine Links, keine Sonderdateien, keine nicht aufgeführten Dateien, und jeder Hash wird geprüft. Packs
  enthalten nur Text (Skripte, Rezepte, Seiten), nie Modellgewichte oder Binärdateien; höchstens 512 MB und 10.000
  Dateien.
- **Vertrauenswürdige Schlüssel:** `configs/community_keys.yml` führt die Schlüssel der Herausgeber, die den
  Community-Index signieren, jeweils mit `key_id`, `pubkey_b64`, `valid_until` (der letzte Tag in UTC, an dem ihre
  Signaturen angenommen werden) und einem optionalen `name`. Die Datei kommt mit jedem Release; bei einem
  Schlüsselwechsel enthält ein Release den alten und den neuen Schlüssel. Eine beschädigte Datei ist ein Fehler und
  wird nie als „keine Schlüssel“ gelesen.
- **Signaturen der Autoren** zeigen, woher ein Pack kommt; vertrauenswürdig machen sie es nicht. Eine Signatur wird
  geprüft, wenn das Pack den öffentlichen Schlüssel enthält (eine kaputte Signatur weist das Pack ab), und der
  öffentliche Schlüssel des Autors (der ganze Schlüssel, nicht seine kurze Schlüssel-ID) wird bei der ersten
  Installation eines Packs dieses Autors festgehalten. Ein späteres Pack mit demselben Autorennamen und einem anderen
  Schlüssel bekommt in der Zusammenfassung eine Warnung.

### Verschlüsselte Packs zum privaten Teilen

Für deine eigenen Rechner oder ein Team lässt sich ein Pack verschlüsseln, statt es zu veröffentlichen
(`llm-cli zoo export ... --to <Schlüsseldatei>` oder `--passphrase`):

- Es wird im Format [age](https://age-encryption.org/v1) von rage verschlüsselt, der Rust-Implementierung von age
  (über `pyrage`): für die age-Schlüssel eines oder mehrerer Empfänger (`age1...`, höchstens 64) oder für eine
  Passphrase (mindestens 12 Zeichen, scrypt), nie für beides, wie age es verlangt. Der Absender signiert die
  verschlüsselte Datei mit Ed25519; so sehen Empfänger, wer sie geschickt hat, und jede Änderung, auch ein
  hinzugefügter oder entfernter Empfänger, fällt auf.
- Die Datei `.lcfpack` ist eine ZIP-Datei mit der age-Datei `pack.zip.age` und der Signatur des Absenders
  `pack.zip.age.sig`. Die üblichen Werkzeuge `age` und `rage` öffnen sie ebenfalls, wenn sie für eine Passphrase
  oder für einen Empfänger aus `age-keygen` oder `rage-keygen` verschlüsselt ist: entpacken, dann
  `age -d -i key.txt -o pack.zip pack.zip.age`, wobei `key.txt` diese Identitätsdatei ist (bei einem Pack mit
  Passphrase ohne `-i key.txt`). Die age-Identität, die das Framework für dich anlegt, verlässt den
  Secrets-Speicher nie; ein Pack, das für sie verschlüsselt ist, öffnet also nur `llm-cli zoo install`. Die
  Signatur des Absenders prüfen die age-Werkzeuge nicht; das Framework schon.
- **Deine Schlüssel:** Bei der ersten Nutzung bekommt jeder Benutzer einen Ed25519-Schlüssel (signiert Packs) und
  eine age-Identität (empfängt verschlüsselte Packs). Sie liegen nur im verschlüsselten Secrets-Speicher.
  `llm-cli zoo keys` gibt die öffentlichen Schlüssel als Text aus (einen Empfänger `age1...` und eine Zeile
  `ed25519:`), die du anderen gibst; ein Empfänger aus `age-keygen` oder `rage-keygen` funktioniert mit `--to`
  ebenso. Ein gespeicherter Schlüssel, der sich nicht lesen lässt, wird nie automatisch ersetzt.
- **Empfangen:** `llm-cli zoo install --file <Pack>.lcfpack` entschlüsselt mit deinem Schlüssel oder mit
  `--passphrase`. `--from <Schlüsseldatei>` (mehrfach möglich) nimmt nur diese Absender an; sonst nennt die
  Zusammenfassung die Schlüssel-ID des Absenders. Ein Pack mit Passphrase, dessen scrypt-Arbeitsfaktor über 2^20
  liegt (1 GiB Speicher), wird abgelehnt, bevor irgendetwas entschlüsselt wird. **Import file...** im Hub öffnet
  Packs, die für deinen Schlüssel verschlüsselt sind; ein Pack mit Passphrase braucht die CLI. Ein verschlüsseltes
  Pack wird nie an die Community übergeben.

### Rezepte

Ein Rezept ist die „Modellvorlage“ des Zoos: das Hugging-Face-Modell in einem gepinnten Commit (nie ein Branch oder
Tag), das Target mit dem Fingerabdruck (sha256 über seine Dateien) und der Version des Target-Ordners, Board, Format,
Quantisierung, Optionen (Importance Matrix, Kontextlänge, Name und sha256 eines Kalibrierungsdatensatzes) und
geprüfte Ergebnisse (Artefaktdatei, Größe und sha256, Framework-Version, Toolchain-Image, optional ein Gerätetest).
Es enthält keine Gewichte: Ein Rezept wird nachgebaut, nie als Modell heruntergeladen.

- Ein Rezept entsteht aus der `build_info.json` eines fertigen Builds, nie von Hand. `build_info.json` hält fest, was
  ein Rezept braucht: den Commit des Modells (der Download wird darauf gepinnt), Fingerabdruck und Version des
  Targets, das Toolchain-Image, die Kontextlänge, den Hash des Kalibrierungsdatensatzes und die Framework-Version.
  Ein Build eines lokalen Modells oder einer ohne Fingerabdruck des Targets kann kein Rezept werden.
- Bei der Installation wird ein Rezept gegen die Capabilities seines Targets geprüft, falls dieses Target installiert
  ist. Ein Hinweis sagt, wenn das Target fehlt oder deine Kopie von der abweicht, mit der das Rezept gebaut wurde.
- **Build** bei einem installierten Rezept im Hub füllt den Builder aus (Modell in seinem gepinnten Commit, Target,
  Format, Board, Quantisierung, Importance Matrix). Du prüfst die Einstellungen und startest den Build; ein Rezept
  wird zu einer normalen Build-Anfrage.

### Der signierte Index

- `index.json` und daneben `index.json.sig` unter der https-Adresse aus der Einstellung `community_index_url`,
  signiert mit einem Schlüssel aus `configs/community_keys.yml`. Ein Index, dessen Signatur sich nicht prüfen lässt,
  wird nicht benutzt.
- Der zuletzt geprüfte Index wird pro Benutzer in `~/.llm-framework/community/` zwischengespeichert. Neu geholt wird
  er, wenn du es willst (`llm-cli zoo list --refresh`, **Refresh** im Hub), wenn nichts zwischengespeichert ist oder
  wenn der Zwischenspeicher älter als ein Tag ist; ohne Netzwerk wird der zwischengespeicherte Index benutzt. Ein
  heruntergeladener Index mit einer niedrigeren Version als der zwischengespeicherte wird abgelehnt; ein älterer Index
  ersetzt also nie einen neueren. Der Zwischenspeicher wird bei jeder Nutzung erneut geprüft, sodass ein entfernter
  oder abgelaufener Schlüssel ihn ungültig macht.
- Der Index führt außerdem gesperrte Packs (über sha256 oder über Art, ID und optional Version, jeweils mit Grund).

### Installieren

`llm-cli zoo install <ID> [--kind target|recipe|knowledge]`, `llm-cli zoo install --file <Pack>` oder **Install**
und **Import file...** im Hub:

1. Eine Installation aus dem Index liest immer zuerst den aktuellen Index, damit keine Sperre verpasst wird; lässt
   sich der Index nicht aktualisieren, wird nichts installiert. Eine Pack-Datei wird im Index nachgeschlagen
   (aktualisiert, wenn er älter als ein Tag ist; ohne Netzwerk der zwischengespeicherte): Führt der Index ihren
   sha256, bekommt sie die Vertrauensstufe dieses Eintrags, sonst `local`.
2. Ein Pack aus dem Index wird über Größe und sha256 aus dem Index gepinnt heruntergeladen.
3. Das Pack wird in einen Quarantäne-Ordner (`.quarantine/` im Installationsordner) entpackt und geprüft: genau die
   aufgeführten Dateien mit ihren Hashes, keine Links, Sonder- oder Binärdateien, und die Prüfungen seiner Art.
   Targets: ein gültiger Block `capabilities` (und `runtime`, falls vorhanden), jedes Image, das eine Dockerfile
   zieht (`FROM`, `COPY --from`, `RUN --mount=...,from=`), per Digest gepinnt, außer es ist ein offizielles
   Basis-Image, kein vorgebautes llama.cpp (Images, kopierte oder heruntergeladene Programme, Pakete), die
   Shell-Sperrliste auf jeder Skriptzeile, die nicht aus der mitgelieferten Vorlage stammt, `bash -n`, keine
   Platzhalter der Vorlage.
   Rezepte: das Schema für Rezepte und die Capabilities des Targets. Wissen: eine Liste von Seiten, jede mit ihrer
   Quelle.
4. Eine Zusammenfassung (Art, ID, Version, Autor, Lizenz, Vertrauensstufe, Quelle, Dateien, Ziel und Hinweise wie
   die Signatur des Autors) wird als reiner Text gezeigt, damit Text aus einem Pack die Vertrauensstufe nicht
   verdecken kann. Installiert wird erst nach deiner Bestätigung; die Standardantwort ist Nein. `--yes` überspringt
   die Frage nur bei Packs, deren Vertrauensstufe aus dem signierten Index kommt, auch bei einer Datei, deren sha256
   der Index führt; ein Pack mit der Vertrauensstufe `local` fragt immer, und ohne Terminal wird nichts installiert.
5. Das Pack kommt mit einer einzigen Umbenennung an seinen Platz, nie über einen vorhandenen Ordner (auch nicht, wenn
   sich nur die Groß- und Kleinschreibung unterscheidet), mit einer Herkunftsdatei `.lcf-provenance.json` (Quelle,
   Vertrauensstufe, Hashes, Signierer, Zeit). Alles, was scheitert, wird entfernt.

Targets kommen nach `targets/`, Rezepte nach `community/recipes/`, Wissen nach `community/knowledge/`. Ein
Wissens-Pack wird direkt nach seiner Installation in die Wissensbasis aufgenommen oder, wenn die Wissensbasis
ausgeschaltet ist, sobald sie eingeschaltet wird.

### Vertrauensstufen

| Stufe | Bedeutung |
|---|---|
| `verified` | im signierten Index: geprüft und unabhängig nachgebaut |
| `community` | im signierten Index: geprüft |
| `local` | eine Datei ohne Eintrag im signierten Index: niemand hat sie geprüft. Die Installation fragt immer, und jeder Build eines solchen Targets zeigt eine Warnung. |
| `revoked` | in der Sperrliste des signierten Index: Die Installation ist blockiert. Installierte Kopien werden von `llm-cli zoo list`, `zoo verify`, `llm-cli doctor` und dem Hub markiert und nicht gebaut, auch nachdem der Schlüssel abgelaufen ist, der die Sperre signiert hat. |

Targets, die mit dem Framework ausgeliefert werden, etwa Rockchip, haben keine Herkunftsdatei; sie sind keine Packs
und haben keine Vertrauensstufe.

### Installierte Packs prüfen

- `llm-cli zoo verify` prüft jedes installierte Pack gegen den zwischengespeicherten Index: gesperrt oder seit der
  Installation verändert. Der Exit-Code ist 1, wenn es ein Problem findet.
- `llm-cli zoo verify <Pack-Datei>` prüft eine Datei, ohne sie zu installieren: Format, Hashes, die Prüfungen ihrer
  Art, eine enthaltene Signatur des Autors und die Datenschutz-Bereinigung (keine Home-Ordner, Adressen, Host- oder
  Benutzernamen, Schlüssel). Es braucht keinen Schlüsselbund; die CI des Community-Repositorys nutzt es.
- `llm-cli doctor` meldet gesperrte Packs als Fehler, veränderte Packs als Warnung und Packs mit der Vertrauensstufe
  `local` als Information ([Gesundheitscheck](#gesundheitscheck-doctor)).

### Community Hub

**Tools > Community Hub öffnen** (englische Oberfläche: *Open Community Hub*) listet die Einträge des Index und die
installierten Packs mit Name, Art, Version, einer farbigen Spalte für die Vertrauensstufe, Autor und Status, mit
einem Suchfeld und Filtern nach Art und Vertrauensstufe. **Refresh** holt den Index neu, **Install** installiert einen
Eintrag des Index nach seiner Zusammenfassung, **Import file...** installiert eine Pack-Datei (auch eine, die für
deinen Schlüssel verschlüsselt ist), und **Build** bei einem installierten Rezept füllt den Builder aus. Ein
gesperrtes Pack zeigt eine abgeschaltete Schaltfläche **Revoked**.

### Export für die Community

- **Wo:** auf der letzten Seite des Modul-Assistenten (**Export for community after creating**: das neue Target), im
  Kontextmenü des Builder-Logs (**Export a finished build for the community...**: die `build_info.json` eines
  fertigen Builds wird zum Rezept) und mit `llm-cli zoo export target <ID>`, `zoo export recipe <Build-Ordner>` oder
  `zoo export knowledge`, jeweils mit `--author <Name>` (ein Pseudonym genügt). Wissens-Packs werden nur mit der CLI
  exportiert.
- **Datenschutz-Bereinigung:** Jede Datei wird auf diesem Rechner bereinigt. Home-Ordner werden zu `~`, IP- und
  MAC-Adressen zu `<ip>` und `<mac>`, E-Mail-Adressen zu `<email>`, JWTs zu `<jwt>`, der Hostname zu `<host>`, der
  Benutzername zu `<user>`, und API-Schlüssel werden geschwärzt. Loopback-Adressen, Image-Digests, `git@`-Remotes und
  Standardkonten wie `root` oder `runner` bleiben, damit ein bereinigtes Target weiter baut. Die Änderungen werden als
  Diff gezeigt, bevor du etwas teilst. Binärdateien werden abgelehnt.
- Das Pack wird wie bei einer Installation geprüft und mit deinem Schlüssel signiert. Es wird nach
  `community/contributions/` geschrieben (oder nach `--out`).
- **Lizenz:** standardmäßig MIT für dein eigenes Target oder Rezept (`--license`; in der GUI eine Liste).
  Wissens-Packs enthalten Seiten anderer Leute: Ihre Lizenz ist `NOASSERTION`, jede Seite behält ihre Quelladresse,
  und eine Seite ohne Quelle wird nie exportiert.
- Ein Target, das du aus einem Community-Pack installiert hast, wird nicht als deins exportiert: Es neu zu
  signieren, würde Autor und Lizenz durch deine ersetzen. Ist das Target eines Rezepts dein eigenes, erinnert ein
  Hinweis daran, das Target ebenfalls zu exportieren. Ein Wissens-Export nimmt die Seiten deiner Wissensbasis mit,
  jede mit ihrer Quelladresse; Seiten aus installierten Community-Wissens-Packs bleiben draußen (ihre Autoren teilen
  sie bereits), und ein Hinweis sagt, wie viele.
- Mit `--to` oder `--passphrase` wird das Pack stattdessen zum privaten Teilen
  [verschlüsselt](#verschlüsselte-packs-zum-privaten-teilen).

### Übergabe als Pull Request

- **Open pull request** im Export-Dialog oder `--pr` bei `llm-cli zoo export` übergibt das Pack an das Repository aus
  der Einstellung `community_repo` (`owner/name`). Die CLI zeigt zuerst die Zusammenfassung und fragt „Submit this
  pack to the public community repository?“, außer `--yes` ist angegeben; ohne Terminal wird nichts übergeben.
- Sind die GitHub CLI (`gh`) und `git` installiert und ist `gh` angemeldet, forkt das Framework das Repository,
  committet das Pack als `contributions/<Art>/<Datei>` auf einem neuen Branch und öffnet den Pull Request mit
  `gh pr create`; `gh` und `git` nutzen ihre eigenen gespeicherten Zugangsdaten. Sonst, oder wenn das scheitert,
  öffnet sich die Upload-Seite des Repositorys im Browser: Leg die Datei dort ab, und GitHub forkt das Repository für
  dich.
- Ohne `community_repo` (heute der Standard) wird nichts übergeben; das Pack liegt fertig in seinem Ordner.

### Werkzeuge für Maintainer

Für das Community-Repository, nicht für Benutzer:

- `scripts/zoo_sign_index.py keygen <Name>` erzeugt einen Ed25519-Signaturschlüssel im Schlüsselbund des
  Betriebssystems und gibt seinen Eintrag für `configs/community_keys.yml` aus (zwei Jahre gültig).
  `entry <pack.zip> --url <https-Adresse>` prüft ein Pack und gibt seinen Eintrag für den Index aus.
  `sign index.json --key <Name> [--previous <alte index.json>]` prüft den Index (seine Version muss höher sein als
  die vorige) und schreibt `index.json.sig`. Um ein Pack zu sperren, nimmst du es in `revoked` auf, erhöhst die
  Version und signierst erneut.
- `scripts/community_repo/validate-contribution.yml` ist eine Vorlage für einen CI-Workflow des
  Community-Repositorys. Bei jedem Pull Request unter `contributions/` nimmt sie nur `.zip`-Pack-Dateien bis 5 MB an
  und führt `llm-cli zoo verify` für jede aus, dessen Datenschutz-Bereinigung auch ein Pack mit einem Schlüssel in
  einer seiner Dateien abweist (der Secret-Scan des Repositorys überspringt Binärdateien und läuft deshalb nicht über
  die ZIP-Dateien). Ein Maintainer prüft und übernimmt das Pack, trägt es in `index.json` ein und signiert den Index
  offline; der Signaturschlüssel kommt nie in die CI. Der Maintainer trägt das Framework-Release ein, mit dem die
  Packs geprüft werden (`LCF_REF`); bis dahin steht in der Vorlage ein Platzhalter, und der Job schlägt fehl.

### Was entfernt wurde

- Das „Swarm“-Hochladen: die XOR-„SwarmCipher“ (eine Verschleierung, die jeder rückgängig machen konnte),
  `llm-cli swarm upload`, der Knopf „Contribute to Swarm“ im Hub und seine Abfrage eines GitHub-Tokens. Alte
  Swarm-Pakete in `community/knowledge/` werden mit einer Warnung übersprungen.
- Das Installieren von Hand kopierter Modulordner aus `community/`. Community-Packs werden mit `llm-cli zoo install`
  oder dem Hub installiert.

---

## Secret-Scan für Mitwirkende

Das Repository schützt sich vor eingecheckten Zugangsdaten. `scripts/secret_scan.py` (nur Standardbibliothek) erkennt
API-Schlüssel von Anbietern, Tokens, private Schlüssel, Zertifikate und Dateien mit Zugangsdaten. Es läuft als
Pre-Commit- und Pre-Push-Hook (`make hooks`) und als CI-Job über die gesamte Historie. Persönliche Zeichenketten wie
deine E-Mail-Adresse oder dein Benutzername kommen in `.git/info/secret-patterns`, das nie eingecheckt wird. Funde
werden geschwärzt ausgegeben. Ein Wert im exakten Token-Format eines Anbieters wird auch dann gemeldet, wenn er
unecht aussieht, weil der Push-Schutz von GitHub den Push ohnehin ablehnen würde. Einzelheiten:
[CONTRIBUTING_DE.md](../CONTRIBUTING_DE.md).

---

## Bekannte Lücken

Was noch nicht funktioniert, damit dich nichts überrascht:

- **Docker-Setup des Frameworks selbst:** `Docker Setup/` sowie `make setup`, `make build`, `make up`, `make gui`
  und `make test-container` funktionieren nicht (`scripts/setup_linux.sh` und `orchestrator/Dockerfile` gibt es
  nicht). Ein gehärtetes Image ohne Oberfläche für CLI und MCP-Server wird gerade gebaut. Die Konvertierungen sind
  nicht betroffen.
- **Windows-Hosts:** Konvertierungen auf einem Windows-Host sind nicht geprüft.
- **Anmeldung im Browser und bei Cloud-Plattformen:** OAuth-Abläufe und die Anmeldeketten von AWS, Google Cloud
  und Azure gibt es noch nicht; Ditto meldet sich mit API-Schlüsseln an, und lokale Engines brauchen keine
  Anmeldung.
- **Laufzeit auf dem Gerät:** Die selbst gebaute llama.cpp-Laufzeit auf einem Board funktioniert über die
  Kommandozeile und ist mit einem nachgebildeten SSH-Kanal getestet, noch nicht auf einem echten Board; eine GUI oder
  ein MCP-Werkzeug dafür gibt es noch nicht.
- **GUI:** Die Reiter Auto-Tuning und Models sind Platzhalter; die lokale Laufzeit wird nur über die CLI gesteuert.
- **Deep Ingest** läuft nur aus dem Modul-Assistenten in der GUI; einen CLI-Befehl dafür gibt es nicht. Einen
  Schnappschuss der Wissensbasis wiederherstellen kann man über GUI oder CLI nicht.
- **Reranking** läuft nur bei Build-Empfehlungen; `rag_rerank_default` nimmt den Chat und das MCP-Tool
  `search_knowledge` hinzu.
- **Gesprochene Ansagen** sind nur mit nachgebildeten Sprachausgaben getestet, nicht mit den echten Stimmen von
  Windows, macOS oder Linux.
- **Community-Zoo:** Ein Community-Repository, ein Index oder ein Maintainer-Schlüssel ist noch nicht veröffentlicht
  (`configs/community_keys.yml` ist leer, `community_index_url` und `community_repo` sind leer); installieren lassen
  sich deshalb nur Pack-Dateien, und ein Pull Request hat kein Repository, an das er gehen kann.
- **Sicherheits-Scan:** Der Trivy-Container bekommt den Docker-Socket, um das Image zu lesen; ein Scan ohne Socket ist
  geplant.
- Kleinere Lücken der CLI stehen in [CLI_Usage_DE.md](CLI_Usage_DE.md#bekannte-einschränkungen).

Was geplant ist: [Roadmap](BLUEPRINT_DE.md#roadmap). Was sich geändert hat: [CHANGELOG](../CHANGELOG_DE.md).
