Version 3.3.0 · Stand 2026-10-30

[English](Features.md) · **Deutsch**

# Funktionen im Detail

Dieses Dokument erklärt, was das Framework im Inneren tut und warum, damit du immer weißt, was deine Werkzeuge
machen. Es beschreibt den Stand des Entwicklungszweigs: Version 3.1.0 und was der [CHANGELOG](../CHANGELOG_DE.md)
seitdem aufführt. Geplantes steht in der [Roadmap](ROADMAP_DE.md), nicht hier.

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
- [Fachbegriffe, Fehlermeldungen und Anrede](#fachbegriffe-fehlermeldungen-und-anrede)
- [Löschen und der Papierkorb](#löschen-und-der-papierkorb)
- [Hardware-Probe und neue Targets](#hardware-probe-und-neue-targets)
- [Gesundheitscheck (Mojo)](#gesundheitscheck-mojo)
- [Community-Zoo](#community-zoo)
- [Nächste Schritte und die Menüleiste](#nächste-schritte-und-die-menüleiste)
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
  Target-Capabilities  Wissensbasis (FastEmbed + Qdrant Edge)
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

GUI: **Build > Targets…** listet jedes Target mit seiner ID (dem Wert für `--target`), Name, Architektur, Boards,
Formaten und Unterstützungsgrad, oder „keine Fähigkeiten angegeben“ (oder dass sein Fähigkeiten-Block ungültig ist);
**Targets…** neben der Target-Liste des Builders öffnet dasselbe Fenster. **Target prüfen** (in diesem Fenster und neben
der Target-Liste des Builders) prüft das gewählte Target wie `llm-cli targets validate`, über dieselbe Funktion
(`TargetManager.validate`), mit frisch gelesener `target.yml`, und nennt jeden Schemafehler mit seinem Feld. Dieselbe
Prüfung läuft, nachdem der Modul-Assistent ein Target angelegt hat und nach der Installation eines Targets aus dem
Community Hub, und ihr Ergebnis wird angezeigt. Ein Target ohne Fähigkeiten bekommt im Builder jedes Format, wie auf der
Kommandozeile, mit dem Hinweis, dass seine Boards, Formate und Quantisierungen nicht geprüft werden; für ein Target mit
ungültigem Fähigkeiten-Block sagt der Hinweis das, und **Target prüfen** nennt jeden Fehler.

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
   Container bekommen es nur lesend: Eine einzelne Datei wird allein eingebunden, nie der Ordner, in dem sie liegt,
   und ein Ordner nur er selbst (ein Modell, das Dateien daneben braucht, etwa externe ONNX-Daten, wird als Ordner
   angegeben). Ein lokales Modell oder ein Datensatz, den Ditto oder ein MCP-Client genannt hat, wird unmittelbar
   vor jedem Containerstart erneut geprüft ([Lokale Dateien für KI-Clients](#mcp-server)).
5. **Sicherheits-Scan:** Trivy durchsucht das Image nach Schwachstellen der Stufen HIGH und CRITICAL (Einstellung
   `enable_security_scan`, festgelegtes Image `aquasec/trivy:0.75.0`). Der Scanner bekommt nie den Docker-Socket:
   Das Image wird als Archiv in einem privaten Ordner des Builds gespeichert, Trivy liest nur diese Datei
   (schreibgeschützt, ohne Capabilities), und das Log sagt, wann das Archiv gespeichert und wieder entfernt wird.
   Ist auf der Cache-Platte kein Platz für das Archiv, wird der Scan mit einer Warnung übersprungen, die die
   Größen nennt. Funde erscheinen als Warnung im Log, nicht als Fehler. Ein Scan, der nicht innerhalb des
   Zeitlimits des Builds (`build_timeout`) fertig wird, wird mit einer Warnung gestoppt.
6. **Importance Matrix** (nur GGUF, wenn gewählt).
7. **Konvertieren und quantisieren** in Containern mit Sandbox ([Build-Sandbox](#build-sandbox)).
8. **Ergebnisse:** Die Modelldatei wird eingesammelt, `build_info.json` wird um die Modelldateien und ihre
   SHA256-Hashes ergänzt, eine `Model_Card.md` mit Hashes und Hinweisen zur Nutzung wird geschrieben, und ein ZIP
   dieser Dateien wird erstellt. All das wird in einem versteckten Ordner neben dem Ausgabeordner vorbereitet und
   erst ganz am Ende hineinverschoben. So hinterlässt ein abgebrochener oder gescheiterter Build keine Dateien im
   Ausgabeordner. Ein früherer fertiger Build in diesem Ordner wird nie ersetzt: ein späterer Build kommt daneben
   (`Q4_K_M-2`, mit `Q4_K_M-2.zip`). Links in der Ausgabe des Builds werden nie verfolgt. Images pro Build werden
   danach entfernt.

Weiteres Verhalten:

- **Abbrechen:** Die Schaltfläche **Cancel Build** im Builder-Tab (aktiv, solange der Build läuft, den der Tab
  gestartet hat; sie fragt nach einer Bestätigung), Strg+C in `llm-cli build start` und das Tool `cancel_build`
  (Ditto im GUI-Chat oder ein MCP-Client, für Builds des eigenen Prozesses) brechen alle über den Build-Service ab.
  Der laufende Container wird gestoppt, auch ein laufender Sicherheits-Scan oder Image-Build, und kein späterer
  Schritt kann den Abbruch rückgängig machen; Schaltfläche und Strg+C warten, bis der Build angehalten hat, und
  sagen das. Ist ein Build gescheitert oder verschiebt er gerade seine Dateien in den Ausgabeordner, lässt er sich
  nicht mehr abbrechen und läuft von selbst zu Ende.
- **Fortschritt und Live-Protokoll:** Solange der Build läuft, den der Builder-Tab gestartet hat, zeigt ein
  Fortschrittsbalken unter dem Builder-Log den Schritt und den Prozentwert aus dem Build-Service, und das Protokoll des
  Builds läuft live darunter mit (Geheimnisse geschwärzt, volle Pfade, reiner Text, jede Zeile einmal; eine KI, die das
  Protokoll liest, bekommt die Ordner als Label). Schritt und Prozentwert enthalten den eigenen Fortschritt des
  laufenden Builds (Vorbereitung, Image, Modell, Module, Archiv), sodass sich der Balken während eines Builds bewegt;
  100 % zeigt er erst, wenn die Anfrage beendet ist. Während das Modell von Hugging Face geladen wird, sagt der
  Schritt, wie weit der Download über alle Dateien des Modells ist, etwa „Fetching model 37 % (1.2 GB of 3.4 GB)“ (nur
  die Bytes, wenn eine Größe nicht bekannt ist), und der Balken bewegt sich mit; die Dateien, die von einem früheren
  Versuch schon da sind, zählen von Anfang an mit, und die Anzeige wird etwa einmal pro Sekunde aktualisiert.
  `llm-cli build start --follow` und `get_build_status` zeigen denselben Schritt. Ein Suchfeld findet Zeilen; ein Filter zeigt alle Zeilen oder nur
  Info, Warnungen oder Fehler. Zeilen, die der Service nicht mehr liefert, werden vermerkt statt still weggelassen, und
  die Ansicht behält die neuesten 20.000 Zeilen.
- **Build-Status über Programme hinweg (gemeinsamer Build-Zustand):** Jedes Programm, das baut (die GUI, ein
  CLI-Aufruf, jeder MCP-Server), schreibt eine Zusammenfassung jeder Build-Anfrage in den Benutzerordner
  (`build_requests/`: Modell als Repo-ID oder Label, Target, Format, Quantisierung, Status, Stufe, Fortschritt, wer
  sie gestartet hat und in welchem Programm; kein Host-Pfad, kein Geheimnis) und je Build einen Eintrag nach
  `<cache_dir>/build_records/`. So sehen die Statusleiste der GUI, `llm-cli build status`, `list_build_requests` und
  `get_build_status` dieselben Builds, auch eine Anfrage, die noch in der Warteschlange eines anderen Programms
  wartet; eine Anfrage, deren Programm endete, bevor sie fertig war, erscheint als unterbrochen.
- **Build-Verlauf (GUI):** **Build > Build-Verlauf…** listet jeden Build jedes Programms (wie `llm-cli build status
  --all`: auch gescheiterte, abgebrochene, unterbrochene und wartende) aus derselben Abfrage
  (`build_history.read_history`); **Details anzeigen** (oder ein Doppelklick) zeigt einen Build wie `build status
  REQUEST_ID`: seine Anfrage mit Fehlern und Hinweisen, jeden Versuch mit seinem Ausgabeordner (voller Pfad) und
  Fehler, und die Korrekturvorschläge, die KI-Clients dafür gemacht haben, als KI-generiert markiert und nie
  ausgeführt. Die Statusleiste nennt dieses Fenster für Builds anderer Programme.
- **Optimierung und Priorität (GUI):** Die Liste **Optimization** des Builders bietet jede Stufe von `--optimization`
  (auch `FAST`), und **Priorität** setzt die Build-Priorität von `--priority` (Vorgabe normal; ein fehlgeschlagener
  kritischer Build beendet die Pipeline).
- **Das Optimierungsprofil des Boards:** Jedes Board eines Targets hat in seiner `target.yml` ein Profil
  (`optimization_profiles`): die llama.cpp-Optionen und Compiler-Flags, die das Build-Rezept für seinen SoC plant
  (von `llm-cli targets validate` gegen das Rezept geprüft), die empfohlenen Quantisierungen, das Speicherprofil und
  die Optimierungsstufe. Der Builder listet die empfohlenen Quantisierungen zuerst und stellt die Liste
  **Optimization** auf die Stufe des Profils, bis du selbst eine wählst; eine Zeile unter der Board-Liste zeigt das
  Profil und die Benchmark-Zeilen des Boards, jede gekennzeichnet als `measured by llmcf (board <Name>, <Datum>)`, `published` oder
  `unmeasured` (die mitgelieferten Zeilen sind Schätzungen ohne Messung und sagen das). `llm-cli build start` und
  KI-Aufrufer (`start_build`, `check_build_request`) nehmen dieselbe Stufe, wenn keine angegeben ist;
  `llm-cli targets boards`, **Build > Targets… > Boards…** und die MCP-Tools `list_targets` und
  `get_target_capabilities` zeigen die Profile und die gekennzeichneten Zeilen. Ein Board-Test füllt die gemessenen
  Werte später (`gen_ts`, `prompt_ts`).
- **Build-Slots:** In einem Programm laufen höchstens `max_concurrent_builds` Builds gleichzeitig (Einstellung, 1
  bis 10, Vorgabe 2); ein weiterer Build wartet auf einen freien Slot (seine Stufe zeigt „Waiting for a free build
  slot“), statt zu scheitern. Ein abgebrochener Build behält seinen Slot, bis sein Container gestoppt ist.
- **Unbekanntes Target:** CLI, Ditto und MCP-Clients lehnen ein unbekanntes Target ab, bevor etwas läuft (die CLI
  mit Exit-Code 2). Eine Anfrage, bei der trotzdem nichts zu bauen bleibt, scheitert mit „Nothing to build“, statt
  Erfolg zu melden.
- **Keine harte Zeitgrenze:** Ein Schritt dauert so lange, wie er dauert. `build_timeout` (Standard 3600 s) ist
  eine weiche Obergrenze: Ein Build, den du gestartet hast, läuft mit einer Warnung darüber hinaus (Abbrechen stoppt
  ihn), einer, den eine KI gestartet hat, stoppt dort und wird nie verlängert. `build_idle_timeout` (Standard
  1800 s) stoppt einen Schritt, der so lange nichts ausgibt. Eine Stufe, die immer neu startet, ohne fertig zu werden,
  und ein Modell, das sich im Funktionstest wiederholt, werden als Schleife gestoppt. Eine KI darf beide
  Einstellungen nur senken.
- **Ein Build endet an seiner Fertig-Marke:** Der Stufen-Runner schreibt als allerletzten Schritt einen
  Abschluss-Eintrag; ein Build ist erst fertig, wenn dieser Eintrag das sagt, keine Uhr kann ihn danach stoppen, und
  ein Container, der ohne ihn endet, ist ein fehlgeschlagener Build. Jeder Build hat einen Endgrund (done, failed,
  cancelled, soft_cap, idle), den der Builder, `llm-cli build status`, `get_build_status` und `build_info.json`
  zeigen; die Wartebefehle der CLI enden daran mit eigenen Exit-Codes ([CLI](CLI_Usage_DE.md#build-status)).
- **Eine Türklingel für jedes Build-Ende:** mit deinem eigenen MQTT-Broker wird jedes Build-Ende einmal nur mit
  festen Wörtern gesendet (Ereignis, Grund, Anfrage-ID), für dein Telefon, deine Hausautomation oder einen
  KI-Client, der wartet; das Framework abonniert nie, ein Broker, der nicht läuft, hält nie einen Build auf, und
  nur du stellst sie ein ([CLI](CLI_Usage_DE.md#config-doorbell)).
- **Ditto kümmert sich um seine Builds:** im Framework-Fenster gibt ein Build, den Ditto gestartet oder auf den es
  gewartet hat, Ditto einen Zug, wenn er endet: es prüft das Ergebnis und schlägt den nächsten Schritt vor oder
  führt ihn im Rahmen seiner Freigaben aus.
- **Wohin ein Build kommt:** siehe [Wohin fertige Modelle kommen](#wohin-fertige-modelle-kommen) unten. Jeder Build
  landet in `<Ordner>/<Target>/<Modell>/<Quantisierung>/`, sein Golden-ZIP daneben.

### Wohin fertige Modelle kommen

Ein fertiger Build (ein Golden-Artefakt: die Modelldateien, `build_info.json`, `Model_Card.md` und das ZIP neben dem
Ordner) wird in einem Ordner deiner Wahl gespeichert (achte Runde, Antwort 3):

- **Der Ausgabeordner** (Einstellung `output_dir`, Standard `~/llmcf/output`; unter Windows
  `C:\Users\<Name>\llmcf\output`). Du legst ihn einmal fest: **File > Output Folder...** in der GUI (der Ordnerdialog
  des Systems) oder `llm-cli config output-folder PFAD`, zum Beispiel auf einen Ordner auf einer großen zweiten
  Platte. Eine KI kann ihn nicht ändern. Builds im alten Ordner bleiben, wo sie sind, und werden weiter aufgelistet.
  Alles unterhalb des Ausgabeordners wird aufgelistet, deshalb kann ein Ordner, der den Build-Ordner, den
  persönlichen Ordner oder den Programmordner des Frameworks enthält (zum Beispiel dein Home-Ordner), nicht der
  Ausgabeordner sein.
- **Der Build-Ordner des Frameworks** (Einstellung `build_dir`, Standard der Ordner `output` im Datenordner, wohin
  Builds bisher kamen): ein Build, den du in der GUI startest, wartet hier, bis er gespeichert ist. Frühere Builds
  bleiben hier und werden weiter aufgelistet.

Wer wohin speichert:

- **GUI:** Ist ein Build fertig, den du gestartet hast, öffnet sich für jedes Golden-Artefakt der Ordnerdialog des
  Systems im Ausgabeordner. **OK** speichert es dort, oder du wählst einen anderen Ordner. **Abbrechen** speichert
  nichts woanders und löscht nichts: der Build bleibt im Build-Ordner, im Tab „Deployment“ als „noch nicht
  gespeichert“ markiert, und **Speichern…** dort speichert ihn später.
- **CLI:** `llm-cli build start --output PFAD` (`-o`, der alte Name `--output-dir` geht weiter). An einem Terminal
  ohne diese Angabe wirst du gefragt, mit dem Ausgabeordner als Vorgabe (`-` lässt den Build im Build-Ordner;
  `llm-cli build save` speichert ihn später). Ohne Terminal kommt der Build in den Ausgabeordner.
- **Ditto und MCP-Clients:** immer der Ausgabeordner; `start_build` sagt das, und `get_build_status` nennt jeden
  fertigen Build über seine Bezeichnung (`output:<Target>/<Modell>/<Quantisierung>`), nie über seinen Pfad auf dem
  Rechner. In `get_build_log` nennt eine Zeile mit einem Pfad auf diesem Computer die Ordner des Frameworks über ihre
  Bezeichnung und jeden anderen Pfad über seinen Dateinamen; Pfade unter `/home/`, `/media/`, `/run/media/`,
  `/Users/` und `C:\Users\` zählen auch dazu (ein lokales Modell auf einem USB-Stick). Deine eigenen Fenster zeigen
  den vollen Pfad.

Speichern verschiebt den Build aus dem Build-Ordner. Auf derselben Platte wird er umbenannt, nichts wird kopiert.
Auf eine andere Platte wird jede Datei kopiert, jede Kopie wird per sha256 mit ihrem Original verglichen (eine
Modelldatei auch mit dem Hash in ihrem Manifest), und erst dann wird die Kopie im Build-Ordner entfernt. Einem Link
im Build wird nie gefolgt, er wird nicht kopiert; fehlt etwas in der Kopie, nennt das Ergebnis es, und die Kopie im
Build-Ordner bleibt. Vor dem
Kopieren wird der freie Platz geprüft, mit der Reserve, die immer frei bleibt. Schlägt eine Prüfung fehl, bleibt der
Build, wo er war, und nur die unfertige Kopie wird entfernt. Ein belegter Name wird nie überschrieben (`-2`, `-3`).
Ein Systemordner, der Programmordner des Frameworks, sein Build-Ordner und sein persönlicher Ordner werden als Ziel
abgelehnt. Ein Build, den du außerhalb des Ausgabeordners gespeichert hast, bleibt aufgelistet, ebenso jeder Build in
einem früheren Ausgabeordner, nachdem du ihn geändert hast (`saved_artifacts.json` im persönlichen Ordner).
`llm-cli deploy list-artifacts`, der Tab „Deployment“ und
`list_artifacts` zeigen den Ausgabeordner, den Build-Ordner und die Ordner, in die gespeichert wurde; eine KI liest
die Bezeichnungen `output:`, `builds:` und `saved:`.

### Gleiche Builds

Vor einem Build suchen `check_build_request`, `llm-cli build start` und das Build-Formular der GUI einen fertigen
Build mit zu 100 %
denselben gewählten Optionen (achte Runde, N11): das Modell und sein genauer Commit (ein lokales
Modell: jede Gewichtsdatei mit Name, Größe und Änderungszeit), das Target und der sha256 seines Ordners, Board,
Format, Quantisierung, Importance-Matrix und der sha256 der Kalibrierdaten, Optimierung, Kontextlänge,
GPU-Nutzung und Aufgabe. Andere Gewichte oder eine andere Quantisierung sind nie gleich. Jeder Build hält diese
Optionen in seinem Manifest fest (`build_options`, mit ihrem sha256); frühere Builds haben sie nicht und werden nie
gemeldet.

- **GUI:** das Build-Formular nennt den früheren Build mit vollem Pfad. **Den vorhandenen Build nehmen** startet
  nichts: Der Ordnerdialog öffnet am Ausgabeordner, und der frühere Build wird samt Golden-ZIP in den Ordner kopiert,
  den du wählst (zuerst wird der freie Platz dort geprüft, jede Kopie per sha256 geprüft, der frühere Build bleibt,
  wo er ist; liegt der gleiche Build dort schon, wird er nicht noch einmal kopiert, und ein anderer Build unter
  diesem Namen wird erst ersetzt, wenn du die vollständige Liste angenommen hast). **Noch einmal bauen** baut
  trotzdem, **Abbrechen** tut keins von beiden. Ist der neueste Commit des Modells noch nicht bekannt, fragt das
  Formular einmal bei Hugging Face nach dem Commit, nur wenn ein früherer Build gleich sein könnte und nie im
  Offline-Modus; ohne den Commit sagt das Formular, dass er sich nicht prüfen ließ.
- **CLI:** `llm-cli build start` stellt an einem Terminal dieselbe Frage (`u` nehmen, `a` noch einmal bauen, `c`
  abbrechen); „nehmen“ kopiert den früheren Build in den Ordner, in den der Build gegangen wäre (`--output`, die
  Antwort auf die Frage oder der Ausgabeordner), mit denselben Prüfungen. Ohne Terminal nennt sie den früheren Build
  und baut wie angegeben.
- **KI-Clients:** `check_build_request` listet solche Builds in `earlier_builds` (`identical`, oder
  `commit_unchecked`, wenn der Commit nicht nachgeschlagen wurde) mit einem Hinweis; die Frage von `start_build`
  sagt, dass sie einen gleichen Build noch einmal baut.

Nichts wird je von selbst übersprungen: du entscheidest (oder die KI, nachdem sie es dir gesagt hat).

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

### Podman statt Docker

Die Container-Engine des Frameworks ist Docker CE. In der ganzen RHEL-Familie (Fedora, RHEL, CentOS Stream, Rocky
Linux, AlmaLinux, Oracle Linux und die übrigen Nachbauten) greift `install.sh` nur dann auf Podman zurück (`podman-docker` und dein rootless `podman.socket`), wenn Docker CE dort nicht geht oder
du die Pakete behältst, die dnf dafür entfernen müsste (siehe [Installation](../README_DE.md#installation)). Das
Framework findet Podman dann selbst: Ist `DOCKER_HOST` nicht gesetzt und antwortet kein Docker-Dienst (weder der
des Systems noch ein rootless Docker), setzt es `DOCKER_HOST` für den eigenen Prozess und die Programme, die es
startet, auf deinen Podman-Socket (`orchestrator/utils/docker_engine.py`, ein Helfer für die Build-Engine, das
Builder-Image, Dittos Container, die Docker-Prüfung und `scripts/build_container_image.sh`). Ein `DOCKER_HOST`, das
du setzt, gilt immer. `podman-docker` verlinkt Dockers Socket-Namen (`/run/docker.sock`,
`$XDG_RUNTIME_DIR/docker.sock`) auf Podmans Sockets; ein solcher Name oder ein Socket, an dem Podman antwortet, zählt
als Podman, nicht als Docker-Dienst. `llm-cli mojo` nennt die Engine, die gerade läuft, und warnt mit dem Ausweg,
wenn `DOCKER_HOST` noch auf einen Podman-Socket zeigt, während ein Docker-Dienst läuft (eine Einstellung aus einer
früheren Podman-Sitzung; dein `DOCKER_HOST` gilt trotzdem). Unter rootless Docker (`docker info` nennt
`name=rootless`) ist uid 0 im Container draußen du, deshalb laufen die Container des Frameworks, die als deine uid
liefen, dort als `0:0`; die übrigen Grenzen der Sandbox bleiben.

Geprüft mit Podman 5.8.2 und buildah 1.43.1 unter Rocky Linux 9.8 und an den Quellen von Podman:

| Unter Podman | |
|---|---|
| Geht | Die Docker-API, die das Framework nutzt (Container, Images, Logs, `docker save` für den Sicherheitsscan); Image-Builds über Podmans Builder (buildah) mit Build-Argumenten, Labels und Zielstufen; `RUN --mount=type=bind` (wie bei BuildKit standardmäßig nur lesend), mehrstufige Builds und Heredocs (buildah ab 1.35; das buildah von Debian und Ubuntu lässt Heredocs weg); `scripts/build_container_image.sh` (es baut mit `DOCKER_BUILDKIT=0`, damit Dockers CLI Podman nie nach BuildKit fragt); die abgeschotteten Container des Frameworks, die als dein Benutzer laufen: Unter rootless Podman bekommen sie Podmans User-Namespace-Modus `keep-id`, deine uid ist also drinnen dieselbe wie draußen, sie können in deine Ordner schreiben, und was sie schreiben, gehört dir (ohne ihn sieht ein Container mit deiner uid deine Ordner als die von root und hält mit "Permission denied" an; geprüft mit Podman 5.8.9 rootless). |
| Vor dem Start abgelehnt | Ein GPU-Build mit einer NVIDIA-GPU: Podmans Docker-kompatible Schnittstelle nimmt nur CDI-Geräteanfragen und übergeht Dockers GPU-Anfrage (`--gpus`) ohne ein Wort, der Build liefe also ohne die GPU. Er wird mit dem Grund abgelehnt; Dittos Container rechnet auf der CPU und sagt warum. |
| Gibt es nicht | BuildKit selbst: Podmans `docker buildx` ist buildah, ohne die Frontends von BuildKit (`# syntax=`), seinen Cache-Export und seine Treiber. Images entstehen stattdessen über Podmans API. Was BuildKit braucht, etwa die geplanten Hailo-Images, wird unter Podman mit dem Grund abgelehnt (`podman_refusal`). |
| Nicht getestet | Ein vollständiger Target-Build und ein Lauf von Dittos Container unter Podman von Anfang bis Ende (die Container-Einstellungen des Builder-Containers und der Build-Stufe liefen gegen Podman 5.8.9 rootless); eine AMD- oder Intel-GPU, die unter rootless Podman als `/dev/dri` weitergegeben wird, auf echter Hardware: Die render-Gruppe des Rechners ist in einem rootless Container nicht abgebildet, deshalb bekommt so ein Container statt der Gruppennummer Podmans `--group-add keep-groups` (deine eigenen Gruppen, wie Podman es beschreibt; es braucht crun). |

Später zu Docker CE wechseln: `sudo dnf remove podman-docker`, dann `sudo ./install.sh` erneut; es installiert
Docker CE aus Dockers Repository für dein System und fragt, bevor dnf etwas entfernt oder ersetzt.

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
| Netzwerkproblem | Versucht es einmal erneut |

Alles andere, auch jeder KI-Vorschlag, wird als manueller Schritt angezeigt und nie ausgeführt. Eine volle Platte ist
ebenfalls ein solcher manueller Schritt, weil das Framework nichts ohne deine Liste löscht (bisher
entfernte ein „Ja“ die verwaisten Images des Frameworks und leerte den ganzen Docker-Build-Cache, auch den anderer
Projekte): Ist Dockers Platte voll, listet die Schaltfläche **Free Docker space...** im Dialog (auch **Tools >
Framework Build Images...**) oder `llm-cli doctor --fix --tier 2` im Terminal die Build-Images des Frameworks mit
Name, ID, Größe und was jedes ist; nichts ist angehakt, und nur die, die du anhakst und bestätigst, werden gelöscht.
Danach startest du den Build neu. Den Docker-Build-Cache leert das Framework nie. Ein erneuter Versuch bekommt eine eigene Build-ID (`<job>_r1`), und beide
Logs bleiben erhalten.

**Vorschläge von KI-Clients.** Ein MCP-Client, der Dittos Aufgabe übernimmt (Prompt `diagnose_build_failure`), kann
dem Framework mit dem Tool `propose_healing_fix` seine eigene Analyse eines gescheiterten Builds übergeben: eine
Zusammenfassung (bis 300 Zeichen), die Ursache und einen manuellen Schritt (je bis 2000), seine Sicherheit und ob der
Schritt für den Build-Rechner oder das Gerät gilt. Er durchläuft dieselben Prüfungen wie Dittos Analyse,
einschließlich des Filters für gefährliche Befehle (ein Vorschlag, der einen Download in eine Shell leitet, Dateien
für alle beschreibbar macht, den Wurzel- oder Home-Ordner löscht oder eine Platte überschreibt, wird abgelehnt, und
nichts wird behalten), und wie jede lokale Änderung bestätigst du ihn, sofern deine Berechtigungen ihn nicht erlauben
(er darf „Ja, und nicht mehr fragen“ anbieten, weil er nur Text speichert, den du liest). Ein Vorschlag wird nur für
einen gescheiterten Build angenommen, höchstens 10 je Build. Er wird in `~/.llm-framework/client_proposals.json`
aufbewahrt und mit dem, der ihn vorgeschlagen hat, und als KI-erzeugt angezeigt: in `get_build_status`
(`client_proposals`, als nicht vertrauenswürdiger Text markiert), in `llm-cli build status <Request>` und in der GUI
(**Tools > Fix Proposals from AI Clients...**, mit einem Hinweis in der Statusleiste bei einem neuen). „Wer“ ist der
Aufrufer, den das Framework kennt (ein lokaler stdio-Client, ein benanntes Token oder Ditto), und der eigene Name des
Clients, als Selbstauskunft markiert. Das Framework führt einen solchen Schritt nie aus, und `resolve_healing` lehnt
ab, ihn anzuwenden.

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
- **Ein Paket ohne Board** (`llm-cli deploy package`; GUI: Tab Deployment > **Nur Paket erstellen…**): das Paket des
  ausgewählten Builds für das gewählte Profil, mit Docker-Images für ein Board ohne Netz, wenn du **Use Docker
  Containerization?** ankreuzt: Die Tabelle darin listet die Container, die ein solches Paket für das Profil enthält
  (die Inference-Engine mit ihrem Image, Einstellung `image_inference_runtime`, und dem Hardware-Zugriff, den das
  Target des Profils ihr gibt), jeder angehakt, also das, was `--docker` verpackt; nimm einen Haken weg, um ihn
  wegzulassen. Wie die CLI nimmt die GUI jeden Pfad: **Anderer Build oder Modelldatei…** fügt der
  Build-Liste einen Build-Ordner oder eine Modelldatei hinzu. Kein Board wird kontaktiert. Die GUI öffnet den
  Ordnerdialog beim Ausgabeordner, wie für jedes Golden Artifact, und nennt den vollen Pfad des Pakets oder, wenn es
  nicht erstellt werden kann, den Grund und den nächsten Schritt. Kopiere es auf das Board und starte dort
  `deploy.sh`, oder deploye es später.
- **Ein vorhandenes Paket** (`llm-cli deploy run PACKAGE_PATH`; GUI: Tab Deployment > **Vorhandenes Paket
  deployen…**): ein früher erstelltes ZIP geht an das Board im Adressfeld, mit derselben Frage zum Host-Schlüssel,
  demselben einmaligen Passwort, demselben Warten und demselben Eintrag bei den vertrauten Geräten wie **Deploy to
  Target**. Die Felder **Port**, **Name** und **Auf das Board warten** des Tabs sind `--port`, `--label` und `--wait`
  der CLI, für beide Schaltflächen.
- **Auf dem Board:** Das Paket kommt in einen privaten temporären Ordner (`mktemp -d`), `deploy.sh` (mit `sh`
  ausgeführt, also auch auf Boards, die `/tmp` mit `noexec` einhängen oder kein bash haben) prüft jede Prüfsumme mit
  `sha256sum -c`, kopiert das Modell nach `~/models` (oder `$MODEL_DIR`), und der temporäre Ordner wird
  gelöscht. Für GGUF gibt es aus, wie du das Modell mit dem Startskript der
  [selbst gebauten Laufzeit](#laufzeit-auf-dem-board-selbst-gebautes-llamacpp) startest, wenn sie auf dem Board
  installiert ist (auf 127.0.0.1 mit dem Schlüssel des Boards), sonst, wie du sie deployst; auf einen
  `llama-server` aus dem `PATH` des Boards verweist es nie. Für RKLLM nennt es die RKLLM-Runtime.
- **Docker-Modus** (`--docker`, Option in der GUI): packt zusätzlich Container-Images ein und startet sie auf dem
  Board. Dieser Modus ist experimentell und in dieser Version nicht geprüft. Ein Image kommt nur ins Paket, wenn es
  per Digest festgelegt ist (`image_inference_runtime: name@sha256:...`) oder auf diesem Rechner gebaut wurde (es
  liegt hier und wurde nie gezogen); ein gezogenes Image, das nur über ein Tag benannt ist, und das voreingestellte
  `llm-framework/inference:local` (ein lokaler Name; das Framework veröffentlicht keine Images), solange du es
  hier nicht gebaut hast, werden mit Grund abgelehnt, bevor etwas kopiert wird. Ein per Digest festgelegtes
  Image, das hier fehlt, wird über seinen Digest gezogen, nie über ein Tag, und ein fehlgeschlagener Export bricht
  das Paket ab, statt das Image wegzulassen. Ein per Digest festgelegtes Image wird unter dem lokalen Namen
  `<Repository>:lcf-<erste 12 Hex-Zeichen des Digests>` eingepackt (`docker load` stellt Registry-Digests nicht
  wieder her), und `deploy.sh` startet jedes eingepackte Image mit `--pull never`: Das Board führt genau das
  eingepackte Image aus, und ein Board, auf dem es fehlt, bricht ab, statt eines aus einer Registry zu ziehen.
  `deploy.sh` installiert das Modell wie ohne Docker (nach `$MODEL_DIR`, standardmäßig `~/models`) und gibt diesen
  Ordner dem Inferenz-Container nur lesbar unter `/models`, wobei `MODEL_FILE` das Modell dort nennt (vorher startete
  der Container ohne das Modell). Pakete früherer Versionen starteten außerdem einen Qdrant-Sidecar (`qdrant-sidecar`), den nichts mehr nutzt: Nach
  einem Deployment schaut das Framework, ob er noch auf dem Board ist, und sagt es dir (im Ergebnis des Deployments
  und in einer Zeile von Mojo Oracle für dieses Board), mit dem Befehl, um ihn dort zu entfernen. Das Framework
  stoppt oder entfernt ihn nie.
- **Vertraute Geräte:** Nach einer erfolgreichen Anmeldung an einem Board, dessen Host-Schlüssel du bestätigt hast
  (ein Deployment in der GUI oder mit `llm-cli deploy run`, eine Prüfung mit `llm-cli scan --device`, ein
  `device runtime deploy`), trägt das Framework es in `~/.llm-framework/devices.json` ein: deinen Namen dafür,
  Adresse, Benutzer, den bestätigten Host-Schlüssel und ob sich der Schlüssel des Frameworks anmeldet (gekoppelt); die
  Geräte-ID wird aus dem Host-Schlüssel abgeleitet. `llm-cli deploy devices list|add|edit|remove` zeigt und verwaltet
  sie (`edit`: eine neue Adresse oder ein neuer Host-Schlüssel, eingefügt oder vom Board gelesen, alter und neuer
  Fingerabdruck gezeigt, "Nein" als Vorgabe, im Audit-Log)
  ([CLI](CLI_Usage_DE.md#deploy-devices)). Nur diese Geräte kann ein KI-Client oder Ditto für ein Deployment nennen,
  und nur, solange die `known_hosts` des Frameworks noch genau diesen Schlüssel enthält; kein Tool vertraut einem
  Gerät, koppelt es oder trägt es ein.
- **Fenster Boards und Geräte** (GUI: **Boards > Boards und Geräte...**): jedes Board, das du untersucht hast oder
  dem du vertraust, in einer Tabelle, mit dem, was nur du siehst (das Konto auf dem Board, den vollen Fingerabdruck
  des Host-Schlüssels und ob `known_hosts` ihn noch enthält, seit wann und wie ihm vertraut wird, das letzte
  Deployment, das System aus seiner Probe, seine Deploy-Einstellungen und die installierte Laufzeit). Seine Knöpfe
  rufen dieselben Funktionen wie die CLI auf, mit denselben Fragen ("Nein" ist die Vorgabe) und Audit-Einträgen:
  **Vertrautes Board hinzufügen...** (`deploy devices add`), **Adresse oder Schlüssel ändern...** (`deploy devices
  edit`: Hostname oder IP-Adresse, Port und der Schlüssel eingefügt oder vom Board gelesen, alt und neu
  nebeneinander; mit einem neuen Schlüssel ziehen Probe, Deploy-Einstellungen, deployte Laufzeit, Deploy-Eintrag und
  "getestet auf"-Einträge des Boards zu seiner neuen Geräte-ID, jeweils vor der Änderung kopiert und geprüft),
  **Vertrauen entfernen...** (`deploy devices remove`:
  ohne es bleibt ein einmal gekoppeltes Board für KI-Deployments erlaubt), **Einstellungen...**,
  **Laufzeit-Plan**, **Status**, **Laufzeit bauen...**, **Laufzeit installieren...**, **Prüfen...**, **Ältere
  Laufzeit-Pakete entfernen...** und **Ältere Builds auf dem Board entfernen...** (die `device`-Befehle unten;
  Löschen im Löschdialog mit der vollständigen Liste) sowie **Dieses Board prüfen (Mojo)** (eine Verbindung zum
  SSH-Port des Boards, keine Anmeldung). Jeder Kontakt mit einem Board und jeder Build laufen im Hintergrund mit
  Abbrechen; ein Passwort wird jedes Mal abgefragt und nie gespeichert.
- **Adresse oder Hostname:** Du gibst ein, was du weißt, die IP-Adresse des Boards oder seinen Hostnamen
  (`myboard.local`). Dem Host-Schlüssel wird je eingegebenem Namen vertraut: ein Board, das du unter beiden Namen
  erreichst, bestätigst du für jeden einmal, und es bleibt dasselbe vertraute Gerät.
- **Wenn das Netzwerk wegbricht** (ein Board nur mit WLAN, etwa das Radxa ZERO 3W): Das Deployment wartet auf das
  Board (Vorgabe 300 s; `--wait` in der CLI) und zeigt, dass es wartet, wie lange schon und wie lange höchstens, mit
  „Abbrechen“ in der GUI und Strg+C in der CLI. Es versucht den SSH-Port alle 5 Sekunden; sobald das Board antwortet,
  meldet es sich mit derselben Prüfung des Host-Schlüssels neu an (ein geänderter Schlüssel wird abgelehnt, auch
  jetzt) und macht weiter. Der Upload geht nach `<Name>.part` und setzt bei den angekommenen Bytes fort, sobald ihre
  sha256 auf dem Board zu denselben Bytes hier passt; sonst beginnt er von vorn. Die sha256 des ganzen Pakets wird auf
  dem Board geprüft, bevor es umbenannt wird. `deploy.sh` läuft auf dem Board für sich (abgekoppelt, seine Ausgabe in
  einer Log-Datei dort) und wird einmal gestartet, nie zweimal, auch wenn die Verbindung abreißt, während es läuft. Ein
  Board, das neu gestartet hat (sein Upload-Ordner ist weg), beendet das Deployment mit dem Grund: neu deployen. Ein
  Fehler auf dem Board selbst, etwa eine volle Platte, beendet es sofort.
- **Warum ein Board nicht erreichbar ist:** vier Grund-Codes, jeder mit dem, was passiert ist, und dem, was du tun
  kannst, auf Englisch und Deutsch, in GUI und CLI: `device_host_down` (nichts antwortet: ausgeschaltet, ein anderes
  Netzwerk, ein Hostname, der nicht aufgelöst wird, oder eine Firewall, die die Verbindung verwirft),
  `device_port_closed` (das Board antwortet, lehnt aber SSH auf diesem Port ab), `device_auth_failed` (die Anmeldung
  wurde abgelehnt), `device_host_key_changed` (ein anderer Host-Schlüssel als der, dem du vertraut hast). Eine KI
  bekommt den reinen Code und einen nächsten Schritt ohne Adresse und ohne Konto.
- **KV-Cache** (Deployment-Fenster, `llm-cli device settings --kv-cache`): `f16`, `q8_0` oder `q4_0` für den Server
  des Boards (`-ctk`/`-ctv`). Standardmäßig automatisch: `q8_0`, wenn neben dem Modell auf dem Board wenig Speicher
  frei bleibt (aus seiner Probe), sonst `f16`; das Fenster sagt, was und warum. Eine Wahl, die du im Fenster
  triffst, bleibt in den Deploy-Einstellungen des Boards; ohne sie bleibt die gespeicherte Wahl, wie sie ist. `f16`
  schaltet den quantisierten Cache aus. Ein im Deployment-Fenster gemachtes Paket nennt ihn im Startbefehl, den sein
  `deploy.sh` ausgibt.
- **Benchmark auf einem Board** (Deployment-Fenster): Für ein Board gebaute Modelle werden auf dem Board selbst
  gemessen, nie auf diesem Computer. Wähle das Board und das Modell darauf (nach einem Deployment ist beides
  eingetragen) und klicke „Auf dem Board messen“: die Laufzeitprüfungen und ein kurzer `llama-bench`-Lauf dort (das
  Board braucht den Modellserver des Frameworks, `llm-cli device runtime deploy`). Vorher wirst du gefragt, mit „Nein“
  vorgewählt und mit Board und Modell genannt, ob ein Modell, das kein Katalogmodell ist, dort geladen werden darf;
  dein Ja wird ins Audit-Log geschrieben. Ein bestandener Lauf wird beim Build dieses Modells vermerkt, und die Build-Liste zeigt „getestet auf
  <Board>“. Das Ergebnis nennt auch die Temperatur des Boards während des Laufs
  ([unten](#laufzeit-auf-dem-board-selbst-gebautes-llamacpp)).
- **Fehler mit Grund:** Paketieren und Übertragen melden, warum sie gescheitert sind (kein Docker, ein nicht
  festgelegtes Image, ein geänderter Host-Schlüssel, der Exit-Status von `deploy.sh`), statt es nur zu protokollieren.
  Ein Paket wird unter einem vorläufigen Namen geschrieben und erst umbenannt, wenn es vollständig ist. Ohne paramiko
  läuft das Deployment über den OpenSSH-Client dieses Computers und kann all das auch (auf das Board warten, den
  Upload fortsetzen, `deploy.sh` selbständig), nur mit dem Schlüssel und der known_hosts des Frameworks, einem
  Zeitlimit für die Anmeldung und Keepalive und der eigenen SSH-Einstellungsdatei des Frameworks (unten) statt
  `~/.ssh/config` und `/etc/ssh/ssh_config`; `ssh` bekommt nie die Standardeingabe des Frameworks (über MCP-stdio ist
  das der Protokollstrom). Ein Passwort braucht dort paramiko (OpenSSH fragt nie danach): Das Ergebnis sagt, dass es
  nicht genutzt wurde. Ein Deployment, das an einen dir gezeigten Fingerabdruck gebunden ist, wird abgelehnt.
  `deploy run` und der Deployment-Tab lesen den Host-Schlüssel des Boards vor dem Deployment mit paramiko, brauchen
  es also weiterhin.
- **Die eigenen SSH-Einstellungen des Frameworks** (`llm-cli deploy ssh-config show|edit|import`; GUI: **Boards >
  Boards und Geräte... > SSH-Einstellungen...**; [CLI](CLI_Usage_DE.md#deploy-ssh-config)): Jeder Aufruf des
  OpenSSH-Clients dieses Computers (das Deploy ohne paramiko; Verbindungen über paramiko nutzen die Datei nicht)
  bekommt die Datei `ssh_config` im Zustandsordner des Frameworks mit `-F`, darum werden weder `~/.ssh/config` noch
  `/etc/ssh/ssh_config` gelesen. Sie wird bei der ersten Nutzung angelegt, ist nur für dich lesbar und enthält nur,
  was du hinzufügst: einen `Host`-Block je Board (seine Adresse oder sein Hostname, wie das Framework es nennt), zum
  Beispiel einen `User` oder einen `ProxyJump` über einen Sprung-Host. Du kannst sie zeigen, den Block eines Boards
  ändern oder einen Block aus deiner eigenen `~/.ssh/config` übernehmen, nachdem er dir gezeigt wurde (diese Datei
  wird nur gelesen). Die Vertrauenseinstellungen bleiben auf der Befehlszeile von OpenSSH, wo der erste Wert gilt
  (strenge Prüfung des Host-Schlüssels, die `known_hosts` des Frameworks, das Null-Gerät als globale, nur sein
  Schlüssel), und Zeilen, die sie ändern oder einen Befehl ausführen würden, werden mit Grund abgelehnt:
  `StrictHostKeyChecking`, `UserKnownHostsFile`, `GlobalKnownHostsFile`, `KnownHostsCommand`, `HostKeyAlias`,
  `HostName`, `LocalCommand`, `RemoteCommand`, `Include`, `Match` (auch `Match exec`), Weiterleitungen und geteilte
  Verbindungen, unter anderem. Ein `ProxyJump` oder ein `ProxyCommand` kommt erst hinzu, nachdem du gelesen hast, was
  er tut (er entscheidet, über welchen Computer oder mit welchem Programm die Verbindung läuft), und Ja gesagt hast;
  „Nein“ ist bei jeder Frage die Vorgabe, und die Änderung wird ins Audit-Log geschrieben. Eine von Hand bearbeitete
  Datei mit einer abgelehnten Zeile stoppt jeden OpenSSH-Aufruf mit Grund (`local_problem`). Dein Ja wird
  festgehalten (der sha256 des genauen Blocks, in `ssh_config.accepted.json` neben der Datei): Ein `ProxyJump` oder
  `ProxyCommand`, der danach von Hand geändert oder geschrieben wurde, bevor das Framework das Ja festhielt, wird nicht
  benutzt; jeder OpenSSH-Aufruf hält an mit dem, was die Zeile tut, und deinem Weg, sie anzunehmen (dieselben Zeilen
  noch einmal in SSH-Einstellungen... oder mit `deploy ssh-config edit` eingeben und Ja sagen). Zeilen, die andere
  Anmeldeschlüssel wählen, laden oder weitergeben würden (`IdentityFile`, `CertificateFile`, `IdentityAgent`,
  `AddKeysToAgent`) oder dem Board eine Umgebung mitgeben (`SetEnv`, `SendEnv`), werden ebenfalls abgelehnt;
  `LogLevel` bleibt erlaubt (es ändert nur, was OpenSSH meldet). Ein Board, das hier über einen `ProxyJump` oder ein
  `ProxyCommand` erreicht wird, geht immer über OpenSSH, nie über paramiko (das keine Einstellungsdatei liest); wo
  OpenSSH den Schritt nicht kann (kein `ssh`, ein Einmal-Passwort, ein an den gezeigten Fingerabdruck gebundener
  Schritt), wird er in klaren Worten abgelehnt. Kein KI-Tool schreibt
  diese Datei; paramiko liest überhaupt keine SSH-Einstellungsdatei. Ein `%` in den eigenen Pfaden des Frameworks
  (sein Schlüssel, seine `known_hosts`) wird dort, wo OpenSSH Tokens ersetzt, als `%%` geschrieben, so dass ein
  Zustandsordner mit `%` im Namen funktioniert; unter Windows wird der Pfad mit Schrägstrichen übergeben (ein
  Leerzeichen darin wird gequotet). Geteilte Verbindungen (`ControlMaster`) bleiben aus.

### Laufzeit auf dem Board (selbst gebautes llama.cpp)

Für GGUF-Modelle baut das Framework llama.cpp für ein Board und installiert es dort (`llm-cli device ...`,
[CLI_Usage_DE.md](CLI_Usage_DE.md#device)). Die RKLLM- und RKNN-Runtime sind Herstellerbinärdateien; sie
installierst du selbst. GUI: **Boards > Boards und Geräte...** (**Laufzeit-Plan**, **Laufzeit bauen...**,
**Laufzeit installieren...**, **Prüfen...**, **Status**, **Einstellungen...**, **Ältere Laufzeit-Pakete
entfernen...**, **Ältere Builds auf dem Board entfernen...**).

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
  Umschalten bleibt jeder ältere Build auf dem Board: Das Framework entfernt keinen von selbst. Die Ausgabe sagt, wie
  viele geblieben sind; `llm-cli device runtime prune GERÄT --on-device` listet sie (voller Pfad auf dem Board,
  Größe, was jeder ist) und entfernt nur die, die du ankreuzt, nach deinem Ja an einem Terminal. Der aktive Build
  und einer, den ein laufendes Programm nutzt, stehen nie in der Liste, und eine KI erfährt nur, wie viele geblieben
  sind. Ein erneut deployter Build, der schon auf dem Board liegt, bleibt dort, auch wenn seine Prüfungen scheitern.
  Den Schlüssel
  in `~/.config/llm-framework/llama-server.key` erzeugt das Startskript auf dem Board unter umask 077; er verlässt
  das Board nie.
- **Startskript** (`llama-server.sh serve|start|stop|status|keygen|version`): POSIX-sh, getestet mit dash und bash. Es
  startet `llama-server` aus seinem eigenen Ordner (nie aus `PATH`) mit `env -i` (HOME, ein fester PATH, die
  C-Locale), `--host 127.0.0.1`, `--api-key-file`, `--no-ui`, `--no-slots`, `--jinja` und `--offline`. Es reicht nur
  Tuning-Optionen mit geprüften Werten weiter (`-c`, `-t`, `-tb`, `-C`, `--cpu-strict`, `-b`, `-ub`, `-np`, `-ngl`,
  `-fa`, `-cram`, `-lm`, `-a`, `--sleep-idle-seconds`, `-ctk`/`-ctv` mit `f16`, `q8_0` oder `q4_0` und `--port` ab
  1024); eine Option der gemeinsamen Verbotsliste
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
  Der KV-Cache der Deploy-Einstellungen (`--kv-cache`, automatisch `q8_0` auf einem Board mit wenig Speicher) kommt
  in den Befehl der Unit; mit dem Startskript gibt das Deployment den Startbefehl damit aus. Ein Deployment stellt
  nie eine Frage.
- **Prüfungen auf dem Board** (auch `llm-cli device runtime verify`), als unprivilegierter Benutzer und nie mit
  sudo: `llama-server --version` und `llama-bench --version` müssen den Pin melden, `llama-bench --list-devices`
  muss die geplanten Geräte zeigen, und `llama-server --tools all` muss abgelehnt werden. Mit `--bench-model` läuft ein
  kurzes `llama-bench -p 64 -n 16 -r 1` auf einem Modell auf dem Board: ein Katalogmodell (an seiner sha256 erkannt)
  sofort, jedes andere Modell erst nach deiner Zustimmung, die ins Audit-Log geschrieben wird (Entscheidung D23). Ein
  `SIGILL` heißt, dass die CPU-Grundstufe des Builds nicht zum Board passt. Ein Deployment führt diese Prüfungen am
  neuen Build aus, bevor er zu `current` wird; ein Build, der sie nicht besteht, läuft also nie als Server des
  Boards. Das Ergebnis liegt in `device/<Geräte-ID>/deployed.json`; `llm-cli device runtime status` sagt, ob der
  deployte Build noch der ist, den Profil und Pin verlangen. Ein bestandener kurzer Benchmark ist der Beweis
  „getestet auf <Board>“: Er wird bei jedem Build dieses Modells im Ausgabeordner, im Build-Ordner des Frameworks
  und in den Ordnern, in die Builds gespeichert wurden, vermerkt (`device_tests.json`, gefunden über die sha256 des
  Modells) und im Deployment-Fenster gezeigt. Der kurze Benchmark läuft auf dem Board selbständig; ein Abbrechen im
  Fenster Boards und Geräte oder des Jobs einer KI und Strg+C an einem Terminal stoppen ihn auch dort: genau den
  Prozess (seine Prozessgruppe, wo das Board `setsid` hat), den das Framework gestartet hat, über die Nummer, die es
  sich gemerkt hat, nie ein Programm, das über seinen Namen gefunden wird; hat das Abbrechen die Verbindung
  geschlossen, stoppt es eine neue Anmeldung mit derselben Prüfung des Host-Schlüssels. Wo das Board `timeout` hat,
  beendet auch das Zeitlimit des Boards selbst (900 s) einen Lauf, den niemand mehr verfolgt.
- **Die Temperatur des Boards** während dieser Prüfungen (bei einem Laufzeit-Deployment, `device runtime verify` und
  dem Benchmark): Das Framework liest die Thermalzonen des Boards (`/sys/class/thermal/thermal_zone*/temp`, ihren Typ
  und ihre Trip-Points) und auf einem Raspberry Pi `vcgencmd measure_temp` und `get_throttled`, mit einem nur
  lesenden Befehl über die Sitzung, die es schon hat, vor dem kurzen `llama-bench`-Lauf und danach; nichts wird
  installiert. Die Grenze ist der eigene `passive`-Trip-Point der Zone (sonst ihr `hot`-Trip-Point), ab dem der Kernel
  den Chip zu bremsen beginnt; eine Zone ohne ihn wird nach einem vorsichtigen Standardwert für ihre SoC-Familie
  beurteilt (aus `/proc/device-tree/compatible`): 80 °C für einen Raspberry Pi und jedes andere Board, 85 °C für
  Rockchip, nie über dem kritischen Trip-Point der Zone minus 10 °C. Die weiche Temperaturgrenze der Firmware (Bit 0x8
  von `get_throttled`) zählt ebenfalls als heiß. Die Prüfzeilen nennen die Temperatur; ein heißes Board bekommt eine
  Warnung mit dem, was hilft (ein Kühlkörper oder ein Lüfter, weniger Threads, eine kleinere Quantisierung, eine Pause
  zwischen den Läufen), und den Hinweis, dass Mojo Oracle sich die Messung merkt: Mojo warnt vor einem Board, das bei
  seinen letzten Prüfungen heiß war, bis eine kühlere Messung sie ersetzt. Ein heißes Board lässt keine Prüfung
  scheitern. Eine KI bekommt dieselben Fakten auf Englisch (`temperature`, `temperature_note`), ohne Pfad und ohne
  Konto. Die Temperatur ist kein Schlüssel der Hardware-Untersuchung: Sie ist ein Moment, keine Eigenschaft des
  Boards.
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

### Experimentell

Ditto ist optional und als experimentell gekennzeichnet: **Nutzung auf eigene Gefahr und Verantwortung. Der Ersteller
von LLMCF haftet nicht.** Das wiederholt den Gewährleistungsausschluss in [LICENSE.txt](../LICENSE.txt) und fügt ihm
nichts hinzu.

- **Abzeichen:** Der Kopf des Chat-Bereichs und die KI-Einstellungen (**Tools > Configure AI Agent (Ditto)...**)
  zeigen **Experimentell** mit diesem Satz; es bleibt.
- **Hinweis, einmal je Benutzer:** Wenn der Chat-Bereich zum ersten Mal erscheint, sagt ein Hinweis oben darin, dass
  Ditto experimentell und optional ist, dass seine Antworten falsch sein können, dass alles, was er tut, weiter über
  deine Werkzeugrechte und Bestätigungen läuft, und den Satz oben. Er bleibt, bis du auf **Verstanden** klickst. Er
  blockiert nichts: Der Chat, die wartenden Fragen und jedes andere Fenster funktionieren, während er angezeigt wird.
  **Ask Ditto for Config** im Builder und der Modul-Assistent nutzen Ditto ohne den Chat-Bereich; ein ausgeblendeter
  Chat-Bereich zeigt sich deshalb für den Hinweis einmal (ohne den Fokus zu nehmen), bis du ihn gelesen hast; danach
  bleibt er ausgeblendet, wenn du ihn ausblendest.
- **Kommandozeile:** Vor dem ersten Befehl `llm-cli ditto ...` oder `llm-cli module generate-ai` an einem Terminal
  geht derselbe Hinweis einmal auf stderr. Ohne Terminal (ein Skript, ein KI-Client mit Shell) wird nichts ausgegeben
  oder gespeichert, also gibt ihn der nächste Befehl an einem Terminal aus. Der Befehl läuft immer.
- Die Zustandsdatei `ditto_notice.json` im benutzereigenen Ordner (`~/.llm-framework`) speichert einen Hash der
  Hinweistexte in beiden Sprachen, wann er gezeigt wurde und wo (`via`: `gui` oder `cli`). Ein geänderter Text wird
  erneut gezeigt. Kein Tool, keine Ressource und kein Prompt zeigt oder beantwortet ihn.

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

### Ehrliches „kann ich nicht“ und ungetestete Hardware

Die Aufgabentexte stehen in einem Modul (`orchestrator/llm/task_prompts.py`), das Ditto und die
[MCP-Prompts](#mcp-server) teilen. Jeder von ihnen sagt dem Modell: Wenn es eine Aufgabe nicht erledigen kann, sagt es
das mit dem konkreten Grund (welche Information fehlt, welche Fähigkeit oder welches Tool es nicht gibt, welche
Berechtigung verweigert ist, welches Gerät oder welche Hardware nicht verfügbar ist, welches Limit erreicht ist) und
behauptet nie ein Ergebnis, das es nicht bekommen hat; dass sich ein Ergebnis nicht auf echter Hardware testen lässt,
ist kein Grund abzulehnen. Modulvorschläge und Build-Empfehlungen dürfen mit `cannot` und einem solchen Grund
antworten. Wenn die eigene Prüfung des Frameworks in den Eingaben der Aufgabe findet, was die Aufgabe braucht (eine
Probe oder das Board mit seinem SoC; Formate, die das Target anbietet), fragt Ditto das Modell noch einmal, und ein
zweites `cannot` erreicht dich als „vom Framework nicht bestätigt“. Ohne Probe und ohne Board gibt es nichts, worauf
ein Modul aufbauen könnte: Das sagt Ditto selbst und fragt kein Modell. Die Healing-Analyse hat kein `cannot`; eine
unklare Ursache kommt mit einer niedrigen Sicherheit.

**Modulvorschläge** dürfen jedes Basis-Image verwenden. Ditto bevorzugt ein Docker Official Image (`debian`,
`ubuntu`) mit den Paketen des Herstellers; für jedes andere Image, zum Beispiel das Toolchain-Image eines Herstellers
auf `nvcr.io`, muss es seinen Grund angeben (`base_image_reason`). Werte, die es geraten hat, listet es in
`assumptions` auf (höchstens 10). Bevor das Modul angelegt wird, zeigen der Modul-Assistent und
`llm-cli module generate-ai` das Basis-Image offen: Registry, Image, Digest (oder, ohne Digest, entweder dass das
Modul das Image über sein Tag nutzt, bei der kurzen Liste von Basis-Images des Modulgenerators, oder dass der
Generator es ablehnt, bis es festgelegt ist),
„Docker Official Image“ oder „andere Registry: ...; fertiges Image seines Herausgebers, vom Framework nicht geprüft“,
dass der Image-Build mit Netzwerkzugriff auf diesem Rechner außerhalb der Build-Sandbox läuft, und den Grund der KI.
Der Assistent übernimmt das vorgeschlagene Image unverändert (ein Image mit „debian“ im Namen wird nicht mehr durch
`debian:bookworm-slim` ersetzt). Das angelegte Modul sagt in seiner Beschreibung, dass es ungetestet ist, und nennt
die Annahmen; seine Unterstützungsstufe ist community.

### Anbieter

| ID | Art | Wo es läuft | Anmeldung heute |
|---|---|---|---|
| `anthropic` | Anthropic-Claude-API | Cloud | API-Schlüssel (`anthropic_api_key` oder `ANTHROPIC_API_KEY`) |
| `openai` | OpenAI Responses API | Cloud | API-Schlüssel (`openai_api_key` oder `OPENAI_API_KEY`) |
| `google` | Gemini, OpenAI-kompatibler Endpunkt | Cloud | API-Schlüssel (`gemini_api_key` oder `GEMINI_API_KEY`) |
| `openrouter` | OpenRouter | Cloud | API-Schlüssel (`openrouter_api_key` oder `OPENROUTER_API_KEY`) |
| `ollama` | Ollama | lokal (Basis-URL einstellbar) | keine |
| `local` | verwalteter llama-server | lokal | Schlüssel pro Start, wird automatisch verwaltet |
| `custom` | jeder OpenAI-kompatible Server | ergibt sich aus der Adresse: Loopback-, private und Link-Local-Adressen gelten als lokal, ein Name nach jeder Adresse, zu der er auflöst (ein `.local`- oder `.lan`-Name nur, wenn diese lokal sind; `localhost`-Namen sind dieser Computer) | API-Schlüssel der Serveradresse (`custom_api_key`) oder keine |

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

- **Wo:** **Anmelden...** in den KI-Einstellungen (GUI), **Security > Anmeldeübersicht...** (GUI) und `llm-cli
  ditto login | logout | auth` (siehe die [CLI-Anleitung](CLI_Usage_DE.md#ditto-login)).
- **Anmeldeübersicht** (GUI: **Security > Anmeldeübersicht...**): die Anmeldung aller Anbieter auf einmal
  (Verfahren, Quelle, Konto oder die letzten vier Zeichen eines Schlüssels, Zustand; Dittos Anbieter mit `*`
  markiert) und wie der Tresor geschützt ist, mit seiner Datei (`ditto auth status`); **Alle prüfen** führt im
  Hintergrund die kostenlose Prüfung jedes Anbieters mit Zugangsdaten aus (`--check`); der Reiter **Alle
  Anmeldeverfahren** listet die Verfahren jedes Anbieters mit ihren Umgebungsvariablen und Anmerkungen (`ditto auth
  methods`). Wer einen Anbieter wählt, sieht sein Anmeldefeld mit einem eigenen Feld **Serveradresse**: Eine dort
  eingegebene (oder von den KI-Einstellungen übergebene) Adresse wird nur für diesen Anbieter gemerkt und macht ihn
  nicht zu Dittos Anbieter (`ditto login --base-url`). Das Framework liest, speichert und übergibt nie Zugangsdaten
  eines Claude-Abos und startet nie den Befehl claude.
- **Schlüssel aus einer Umgebungsvariable:** Jede Schlüsselkarte nennt die Umgebungsvariablen des Verfahrens und
  seine Anmerkung und bietet **Den Schlüssel aus <VARIABLE> im Tresor speichern** an, solange eine gesetzt ist
  (vorher geprüft, außer du nimmst den Haken bei **Vorher prüfen (kostenlos)** weg; `ditto login --from-env`). Der
  Schlüssel geht im Hintergrund von der Variable in den Tresor und nie in ein Feld.
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
  entfernst. Ein Server ohne Anmeldung (Ollama, `custom` ohne Schlüssel) hat **Vergessen**, das seine gemerkte
  Adresse und sein gemerktes Verfahren vergisst (`ditto logout`); beides fragt vorher, „Nein“ ist vorgewählt.
- **Kein nutzbarer Schlüsselbund:** Der Bereich zeigt die Auswahl für den Schutz (Passphrase, Credential-Helper
  oder die unverschlüsselte Schlüsseldatei nach einer Bestätigung), und die GUI zeigt dieselbe Auswahl beim Start,
  bevor sie aufgibt; eine Passphrase wird in einem Dialog abgefragt. Eine Wahl, die den vorhandenen Tresor nicht
  öffnen kann (einen, den der gesperrte Schlüsselbund schützt), wird nicht behalten. Die CLI endet mit Exit-Code 5
  und nennt `llm-cli ditto auth storage --use ...`. Für dich wird nichts gewählt.
- **Schlüsselspeicher** (GUI: **Security > Schlüsselspeicher...**): der Schutz des Tresors und seine Datei
  jederzeit, auch während er funktioniert (`ditto auth storage`); **Anders schützen** wechselt zum Schlüsselbund des
  Betriebssystems, zu einem Credential-Helper mit seinem Befehl, zu einer Passphrase oder zur unverschlüsselten
  Schlüsseldatei (nach der Bestätigung und der Frage der CLI, „Nein“ ist vorgewählt; `--use`), und **Schlüssel
  erneuern...** fragt vorher (`ditto auth rotate-key`).
- **Reaktionsschnell:** Jeder Zugriff auf Tresor, Schlüsselbund oder Netz aus dem Anmeldebereich und für den Kopf
  des Chats läuft in einem Hintergrund-Thread.

### Aufrufe, Wiederholungen und Ersatzkette

- Die eigenen Wiederholungen der SDKs sind aus; die Anbieterschicht wiederholt Anfragen bei Ratenlimits und
  Ausfällen bis zu zweimal und wartet auf ein `Retry-After` des Anbieters von bis zu 30 s (Einstellung
  `ai_max_retry_wait`). Jede Anfrage darf bis zu 600 s dauern (`ai_timeout`).
- **Ersatzkette** (`ai_fallback_chain`, z. B. `local, ollama:qwen3.5:4b`): Lehnt das Modell ab oder ist der Anbieter
  nicht erreichbar, wird der nächste Eintrag versucht. Nicht eingerichtete Einträge werden übersprungen.
- **Offline-Modus** (`offline_mode`): Es werden nur lokale Anbieter genutzt; ein Cloud-Anbieter wird mit einer
  klaren Meldung abgelehnt. Auch die Wissensbasis lädt im Offline-Modus nichts herunter. „Lokal“ folgt einer Regel:
  Ein Servername zählt nach den Adressen, zu denen er auflöst (ein `.local`- oder `.lan`-Name
  nur, wenn er zu einer Adresse in deinem eigenen Netz auflöst; ein öffentlicher Name wie der eines Reverse Proxys
  ist nicht lokal; ein Name mit Adressen in deinem Netz und im Internet zählt als lokal, und das Framework verbindet
  sich nur mit denen in deinem Netz), und eine Tailscale- oder Headscale-Adresse nur, wenn ihr Steuerserver und jedes
  Relay in deinem eigenen Netz sind und sie zu einem Gerät deines Tailnets gehört. Ollama und ein eigener Server
  werden nach ihrer Adresse beurteilt, und im Offline-Modus verbindet sich das Framework nur mit den geprüften
  Adressen. Was sonst im Offline-Modus als lokal zählt, ein VPN, ein Host oder ein Adressbereich, entscheidest du in
  **Datei > Einstellungen… > Lokales Netz** oder mit `llm-cli config local-network`, jeweils mit einer einfachen
  Erklärung, was dann dein Netz verlassen kann; eine KI kann das weder lesen noch setzen. Der Datenschutzfilter
  bleibt für alles an, was du dort hinzufügst: Er folgt dem, wo ein Server steht, nie deinen Ausnahmen. Ein Download
  der Laufzeit (ein Modell, die festgelegte Quelle), ein Qdrant-Server, das **Prüfen** der Anmeldung und die
  llama.cpp-Überschreibung eines Image-Builds erreichen im Offline-Modus nur dein eigenes Netz, und die
  Laufzeit-Tools und Paketinstallationen einer KI sehen nach deiner Antwort noch einmal auf den Offline-Modus. Ein
  Deep Ingest, den du im Offline-Modus startest, liest nur Seiten in deinem eigenen Netz (nie einen Metadatendienst
  einer Cloud); der Crawl einer KI liest dann nichts.
- **Offline bauen** (ohne Netz: kein Internet, vielleicht ein lokales Netz): Das Cross-Kompilieren und die
  Target-Artefakte brauchen das Build-Container-Image des Targets, das Docker-Image mit seiner Werkzeugkette. Ein
  früher mit denselben Einstellungen gebautes Image wird genutzt; ohne eines baut der Offline-Modus es, wenn jede
  Quelle lokal ist: die Basis-Images auf diesem Computer oder in einer Registry (oder einem Docker-Registry-Spiegel)
  deines Netzes, llama.cpp von deinem Spiegel oder aus der lokalen Kopie des festgelegten Commits, apt und pip nur
  über den apt-Spiegel, den apt-Cache oder den pip-Index, die du in deinem Netz einstellst
  (`llm-cli build image sources`, **Build > Build-Container-Images...**), jeder andere Download in deinem Netz. Sonst
  hält der Build vor allem Langsamen an und nennt jede fehlende Quelle und wie man sie bereitstellt
  (`llm-cli build image plan TARGET` zeigt denselben Plan vorher; eine KI sieht ihn als Labels über
  `get_target_capabilities` mit `check_image`). Ein fertiges Image kommt als Datei mit seiner sha256 auf einen
  anderen Offline-Rechner (`llm-cli build image export` / `import`); der Import lehnt eine Datei ab, deren sha256 oder
  deren Labels nicht zu einem Build dort passen. Nur du setzt die Quellen und bringst Images hin und her; eine KI
  bekommt `needs_person`.
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
Überschreiben einer Platte (`mkfs`, `dd` auf eine Platte) und eine Fork-Bombe. Der Filter liest den Text so, wie er
geschrieben ist, und so, wie du ihn sehen wirst (normalisiert, unsichtbare Zeichen entfernt), damit ein Zeichen
ohne Breite oder Buchstaben in voller Breite einen solchen Befehl nicht verstecken. Ein Korrekturvorschlag, den ein
KI-Client mit `propose_healing_fix` macht, durchläuft denselben Filter und wird in dieser bereinigten Form
aufbewahrt.

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

### Der Chat-Bereich

Der Chat sitzt in einem Bereich rechts im Hauptfenster; der frühere Reiter **AI Chat** ist dorthin umgezogen, und
nichts, was er konnte, geht verloren. F9 oder **Ansicht > KI-Chat-Bereich** zeigt oder verbirgt ihn, **Ansicht > Im
Chat schreiben** (Strg+Umschalt+C) setzt den Cursor ins Nachrichtenfeld, ein Klick auf Ditto in der Werkzeugleiste
zeigt ihn, und die Schaltfläche **KI-Chat (3)** in der Werkzeugleiste zählt neue Nachrichten und wartende Fragen. Du
kannst den Bereich nach links verschieben oder frei schweben lassen; wo er sitzt, ob er gezeigt wird, sein Filter und
der neueste Eintrag, den du gesehen hast, stehen in `~/.llm-framework/gui_state.json` (eine beschädigte Datei ergibt
die Vorgaben).

- **Was ankommt:** deine Nachrichten, Dittos Antworten und Werkzeug-Hinweise, Hinweise (ein Korrekturvorschlag eines
  KI-Clients, die Nutzung einer Dauerfreigabe) und die Einträge darüber, was KI-Apps getan haben, aus jedem Prozess
  des Frameworks (der GUI, der Kommandozeile, jedem MCP-Server). Der Bereich liest den signierten Chat-Verlauf einmal
  pro Sekunde und wartet nie darauf; ein Eintrag, der sich nicht prüfen lässt, wird nicht gezeigt, und der Bereich
  sagt, wie viele übersprungen wurden. Ohne den Verlauf zeigt er Dittos Gespräch wie bisher und sagt, dass neue
  Nachrichten nicht behalten werden.
- **Was KIs getan haben** (Welle 6): Was Ditto oder eine KI-App mit den Werkzeugen des Frameworks
  tut, wird zu einem signierten Eintrag, geschrieben von dem Prozess des Frameworks, der die Arbeit getan hat: ein
  Aufruf, der etwas geändert hat („Hat einen Build gestartet.“, „Hat Einstellungen geändert: language.“), das Ende
  einer Arbeit, die eine KI gestartet hat (ein Download, eine Übertragung, ein Deep Ingest, ein Build, mit dem Ordner
  eines fertigen Builds als Bezeichnung), die Nutzung einer Dauerfreigabe, eine Frage, die auf dich wartet, und wie
  sie endete, und eine Arbeit, die eine KI nicht tun konnte, mit dem Grund in Worten des Frameworks. Ein Eintrag nennt
  das Fenster, das das Ergebnis zeigt, aus einer festen Liste, und aus einem Eintrag läuft nie etwas. Deine eigene
  Arbeit in der GUI und auf der Kommandozeile erzeugt keine Einträge; eine App wird mit der Verbindung genannt, unter
  der das Framework sie kennt, ihr eigener Name nur als ihre Angabe. Dieselbe Ablehnung derselben App wird einmal in
  30 Sekunden geschrieben; das Audit-Log behält jeden Aufruf.
- **Wartende Fragen** kommen aus den ausstehenden Freigaben, nie allein aus Chat-Einträgen, und bleiben unten unter
  **Braucht dich**: wer fragt, was, und wie viel Zeit bleibt, neu gezählt, solange die Frage wartet. **Im
  Freigabe-Fenster öffnen…** entscheidet sie dort. Eine neue Frage zeigt einen verborgenen Bereich, ohne den
  Tastaturfokus zu nehmen, und holt die Tastatur nie von der Frage weg, auf der du gerade bist; das Zeigen lässt sich
  im Menü **⋯** des Bereichs abschalten (die Einstellung `chat_panel_auto_show` kann keine KI ändern).
- **Filter:** Alles, Braucht dich, Entscheidungen, Chat mit Ditto. Routine-Entscheidungen einer App innerhalb von
  zehn Minuten werden zu einer Zeile zusammengefasst („Claude Code hat 6 Dinge erledigt“, **Zeigen**). **Neu, seit du
  zuletzt geschaut hast** markiert den ersten neuen Eintrag, mit einer Zusammenfassung, die aus den Einträgen selbst
  gezählt wird, nie aus dem, was eine KI geschrieben hat.
- **Nur reiner Text:** Was eine KI, eine App oder ein Dateiname geschrieben hat, erscheint als reiner Text,
  versteckte Zeichen als Escape-Folgen: keine Formatierung, keine Bilder, nichts Anklickbares. Eine Adresse in einem
  Text öffnet sich nur über ihr Kontextmenü (**Adresse öffnen…**), nach einer Frage, die die ganze Adresse zeigt.
  Deine Nachrichten stehen rechts; Dittos links mit seinem Bild; die einer KI-App links unter dem Namen, unter dem das
  Framework sie kennt, mit dem Namen, den die App sich selbst gibt, klein darunter (eine App, die sich Ditto, das
  Framework oder das System nennt, wird als „nennt sich so“ gezeigt). Hinweise des Frameworks gehen über die ganze
  Breite und schreiben „Warnung:“ aus; Werte von außerhalb des Frameworks stehen in Anführungszeichen und bleiben in
  ihrer Zeile (auch ein Werkzeugname, den sich Dittos Modell ausgedacht hat). Ein Hinweis darauf, was eine App getan hat (ein Korrekturvorschlag, die Nutzung einer
  Dauerfreigabe), steht unter dem Namen dieser App, als „vom Framework geschrieben“ markiert; nur Einträge von Ditto
  bekommen Dittos Bild. KI-Text in einem Eintrag des Frameworks steht in einem Kasten mit der Überschrift „In eigenen Worten
  (KI-Text, vom Framework nicht geprüft)“.
- **Sprechende Namen:** Kennungen, die das Framework vergeben hat (ein Auftrag, ein Build, eine Frage), erscheinen in
  KI-Text mit ihrem Namen «so»; **Ursprüngliche Worte zeigen** zeigt den Text genau so, wie er geschrieben wurde.
  Kennungen in Code behalten ihre Form und bekommen eine Legendenzeile. Nur das Framework schreibt «»: Guillemets im
  Text selbst erscheinen als Escape-Folgen (`\u00ab`, `\u00bb`), sodass eine KI ihre eigenen Worte nicht wie einen
  Namen aussehen lassen kann, den das Framework nachgeschlagen hat.
- **Tastatur und Bildschirmleser:** Enter sendet, Umschalt+Enter fügt eine Zeile ein, Pfeil hoch in einem leeren
  Nachrichtenfeld geht zum neuesten Eintrag, Pfeil hoch/runter und Pos1/Ende gehen zwischen den Einträgen, Escape
  zurück ins Nachrichtenfeld. Jeder Eintrag hat einen zugänglichen Namen (Absender und Zeit), und neue Einträge werden
  Bildschirmlesern angesagt, Fragen mit Vorrang. Keine Taste gibt etwas frei.
- **Optionen für diese Frage** (über dem Nachrichtenfeld; `llm-cli ditto ask --no-tools/--provider/--model`):
  **Ohne Werkzeuge antworten**, ein Anbieter und ein Modell nur für die nächste Frage. Die gespeicherten
  KI-Einstellungen bleiben unverändert (die Frage geht an einen Ditto mit diesen Werten über deinen Einstellungen), der
  Bereich sagt, was die Frage verwendet hat, und die Optionen werden nach dem Senden zurückgesetzt. Einen Anbieter, den
  der Katalog nicht kennt, lehnt er ab wie die Kommandozeile.
- **Chat leeren…** fragt zuerst und nennt für Dittos Gespräch (`chat_history.json`, so wie die Datei es jetzt hält,
  auch wenn ein anderes Fenster oder das Terminal es inzwischen gespeichert hat) und für die Chat-Nachrichten im
  Chat-Verlauf (`chat_feed.sqlite`) den vollständigen Pfad, den Dateinamen, die Größe und was es ist; **Nein, alles
  behalten** ist die Vorgabe. Die Einträge darüber, was KI-Apps getan haben, werden nur im Bereich ausgeblendet, nie
  gelöscht. Das Leeren wird ins Audit-Log geschrieben, bevor etwas gelöscht wird. Nichts wird gelöscht ohne Audit-Log,
  wenn sich Dittos Gespräch geändert hat, während du gefragt wurdest, oder wenn dieses Fenster ein Gespräch hält, das
  seine Datei nicht zeigt (neu starten und noch einmal versuchen).

Eine Frage direkt im Chat mit Ja oder Nein beantworten, „Warum?“ und Rückgängig kommen mit späterer Arbeit; bis
dahin entscheidet das Freigabe-Fenster.

### Die Hände der KI im Fenster (das GUI-Tool)

Die Tools des Frameworks sind die Hände einer KI, auch im Fenster. Solange das Fenster des Frameworks offen ist,
können Ditto und jede KI-App, die den MCP-Server nutzt (Claude Code, Claude Desktop, VS Code, Cursor und andere), mit
einem Tool auf seinen offenen Formularen arbeiten, `gui`: dem Build-Formular im Builder-Tab, dem Assistenten für
Target-Module (Seite für Seite) und dem Dialog der KI-Einstellungen. Ditto und die KI-Apps nutzen dasselbe Tool mit
denselben Regeln.

| Aktion | Art | Was sie tut |
|---|---|---|
| `read_form` | liest | Was du in einem offenen Formular eingegeben hast: jedes Feld mit seinem Wert, wer es ausgefüllt hat (`you`, `ditto`, `another_app` oder niemand), ob du es gewählt hast und ob es schreibgeschützt ist. Ohne Formular: die offenen Formulare und die Fenster, die `show_window` öffnen kann |
| `fill_form` | ändert | Füllt Felder eines offenen Formulars sichtbar aus (fragt standardmäßig, wie jedes neue Tool, das etwas ändert) |
| `wizard_state` | liest | Die aktuelle Seite des Assistenten, alle Seitentitel und die Felder dieser Seite |
| `wizard_open`, `wizard_fill`, `wizard_next`, `wizard_back` | ändert | Öffnet den Assistenten, füllt die aktuelle Seite aus, geht zur nächsten oder vorigen Seite (füllt vorher die aktuelle Seite aus, wenn Werte angegeben sind) |
| `wizard_docs` | ändert | Öffnet Deep Ingest mit den Dokumentations-Links des Frameworks für die Hardware; du startest das Sammeln |
| `show_window` | liest | Zeigt einen Tab oder ein Fenster: den Builder- oder Deployment-Tab, den Chat-Bereich, den Assistenten, die KI-Einstellungen, den Hugging-Face-Browser, den Community Hub oder die Wissens-Snapshots. Es ändert nichts |

Die lesenden Aktionen werden wie Lese-Tools entschieden (standardmäßig erlaubt), die anderen wie ein Tool, das etwas
auf diesem Computer ändert (standardmäßig mit Frage). Deine Einstellung für `gui` in Tool Permissions gilt für alle
seine Aktionen, für jeden Aufrufer einzeln: Ditto, alle lokalen stdio-Clients oder ein benanntes MCP-Token. Ein neues
MCP-Token kann das Fenster erst nutzen, wenn du seine Berechtigungen festgelegt hast. Eine Frage, die ein Client nicht
zeigen kann, wartet im Framework auf dich (wartende Freigabe), wie bei jedem anderen Tool.

Die Regeln sind in jedem Formular gleich (`orchestrator/tools/gui_surface.py`):

- **Geheimnisse:** Eine KI liest einen Schlüssel nur als „gesetzt“ oder „nicht gesetzt“ und füllt nie einen aus. Die
  KI-Einstellungen zeigen den Anbieter, die Basis-URL, die Datenschutzstufe und die Ersatzkette, aber nur du änderst
  sie: Sie entscheiden, wohin deine Daten gehen. Eine KI darf die Wissensbasis aus- und den Offline-Modus einschalten,
  nie umgekehrt (dieselbe Regel wie bei `update_settings`).
- **Pfade:** Eine lokale Datei erreicht die KI als Label (`models:tiny.gguf`) oder mit ihrem Dateinamen, nie mit dem
  Home-Ordner. Ein Home-Ordner in Text, den du getippt hast (auch der eines anderen Kontos, `/home/<Name>` oder
  `C:\Users\<Name>`), erreicht die KI als `~`. Eine KI nennt eine lokale Datei mit ihrer ID aus `list_local_files`
  (`lf_...`) oder als `<Label>:<relativer Pfad>`, nie mit einem Pfad.
- **Deine Werte gehören dir:** Einen Wert, den du getippt, gewählt oder angeklickt hast (auch eine Datei, die du in
  einem Dateidialog gewählt hast, und die Werte eines Rezepts), überschreibt eine KI nie. Eine KI darf im Chat einen
  anderen vorschlagen; **Ask Ditto for Config** im Builder füllt das Formular nach denselben Regeln, und das
  Builder-Log nennt jeden Wert, den es nicht eingetragen hat, und warum. Ein Feld, das du gerade bearbeitest (es hat
  den Tastaturfokus), wird nie beschrieben. Würde ein Ausfüllen über die Logik des Formulars ein Feld ändern, das du
  gewählt hast (ein anderes Target setzt das Board zurück), wird es ganz zurückgenommen und abgelehnt. Was nie
  ausgefüllt werden kann, wird abgelehnt, bevor du gefragt wirst; beim Ausfüllen wird alles noch einmal geprüft.
- **Wer es ausgefüllt hat, sichtbar und rückgängig zu machen:** Das Formular kommt nach vorn, und jedes ausgefüllte
  Feld bekommt einen Rahmen und eine Zeile über dem Formular, die nennt, wer es ausgefüllt hat. Dittos Felder haben
  einen lila Rahmen. Die Felder einer KI-App haben eine andere Farbe und ihren Namen: den Namen ihres MCP-Tokens (den
  du vergeben hast) oder den Namen, den die App sich selbst gibt, in Anführungszeichen, als ihr eigener Name markiert,
  den niemand geprüft hat. Eine App sieht nie aus wie Ditto, auch wenn sie sich „Ditto“ nennt. **KI-Änderungen
  rückgängig machen** (hat nur Ditto ausgefüllt: **Dittos Änderungen rückgängig machen**) nimmt alle KI-Änderungen
  zurück; ein Feld, das du danach geändert hast, behält deinen Wert. Das Rückgängigmachen steht im Audit-Log, mit
  dem, der ausgefüllt hatte.
- **Nichts wird abgeschickt:** Kein Feld startet einen Build, speichert ein Modul oder eine Einstellung. **Start
  Build**, **Finish** im Assistenten und **Save** bleiben deine Klicks, mit ihren eigenen Prüfungen und Fragen. Ein
  Modul, das eine KI im Assistenten ausgefüllt hat, wird als KI-erstellt und ungetestet markiert, und seinen ersten
  Build siehst du einmal. Was der Assistent teilt (angehängtes gesammeltes Wissen, der Export für die Community),
  entscheidest du.
- **Audit:** Jeder Aufruf geht wie jedes andere Tool durch die Tool-Registry des eigenen Programms des Aufrufers:
  Berechtigungen, Grenzen (30 Ausfüllvorgänge und Assistenten-Schritte pro Minute; Lesen zählt bei den Lese-Tools) und
  ein Audit-Eintrag mit dem Aufrufer.

**Wie eine KI-App das Fenster erreicht.** Der MCP-Server einer KI-App läuft in einem eigenen Prozess. Solange das
Fenster offen ist, schickt dieser Server die Aufrufe des GUI-Tools über eine private Brücke an das Fenster
(`orchestrator/tools/gui_bridge.py`), wo derselbe Code wie für Ditto die Arbeit macht. Nur du erreichst diese Brücke:
Sie ist ein Socket im privaten Ordner `gui_bridge` deines Benutzerordners (Linux und macOS) oder eine Named Pipe mit
zufälligem Namen (Windows), und beide Seiten weisen einen zufälligen Schlüssel aus einer Datei nach, die nur dein Konto
lesen kann, bevor etwas gesendet wird. Ein Programm eines anderen Kontos auf demselben Computer bekommt nichts. Ist
kein Framework-Fenster offen, antwortet `gui` mit dem Blocker `gui_not_open` und bittet dich, das Framework-Fenster zu
öffnen; das Tool bleibt aufgeführt, damit die App dir sagen kann, warum. Das Fenster, das zuerst geöffnet wird, bedient
die KI-Apps; wird es geschlossen, übernimmt ein anderes offenes Framework-Fenster innerhalb weniger Sekunden. Es ist
das vierzigste Tool, das ein Client aufführt, so bekommen Clients, die höchstens 40 Tools nehmen, trotzdem alle.

---

## Dittos Tools

Im Chat (GUI) und mit `llm-cli ditto ask` kann Ditto dieselben Tools wie MCP-Clients nutzen:

| Tool | Stufe | Was es tut |
|---|---|---|
| `list_targets` | lesen | Targets mit Boards, Formaten und Quantisierungen |
| `get_target_capabilities` | lesen | Was ein Target bauen kann und wie weit ihm vertraut wird (mitgeliefert, geprüft, Community, KI-erstellt und so weiter); mit `check_files` auch, ob seine Moduldateien vollständig sind |
| `get_hardware_probe` | lesen | Die neuesten Hardware-Probes mit dem jeweils passenden Target, oder die Werte einer Probe (externer Text) über ihre Probe-ID |
| `probe_local_hardware` | lokal schreiben | Die Hardware dieses Rechners als Job messen und als Probe `local` behalten |
| `create_target_module` | lokal schreiben | Ein neues Target-Modul aus einer Definition anlegen (immer mit Frage; Basis-Image per Digest festgelegt; als ungetestet und KI-erstellt markiert) |
| `list_build_requests` | lesen | Build-Anfragen mit Status, 50 pro Seite; die anderer Framework-Prozesse nur lesbar |
| `get_build_status` | lesen | Status, Fortschritt, Fehler, ein offener Self-Healing-Vorschlag; mit `wait_seconds` (höchstens 50) wartet es auf eine Änderung |
| `read_build_log` | lesen | Die letzten Zeilen eines Build-Logs (Geheimnisse geschwärzt; höchstens 1000, zurückblättern mit `before_line`) |
| `list_artifacts` | lesen | Fertige Builds mit ID und Label (nie ein Host-Pfad), mit Größe und Hash |
| `get_artifact_details` | lesen | Manifest, Model Card und Dateien eines gebauten Modells (externer Text) |
| `check_build_request` | lesen | Probelauf von `start_build`: Commit, Downloadgröße, Speicherplatz, Hardware-Schätzung, Lizenz, Zugangsbeschränkung; startet nichts |
| `run_mojo_oracle` | lesen | Der Gesundheitscheck (Mojo Oracle): jeder Befund mit Ort (eine Bezeichnung), warum er wichtig ist und was Mojo Miracle dagegen tun kann ([Einzelheiten](#gesundheitscheck-mojo)) |
| `run_mojo_miracle` | lokal schreiben | Repariert ein Problem, das Mojo Oracle als reparierbar meldet (eine harmlose Reparatur); immer mit Frage, löscht nie, keine Vollmacht |
| `search_knowledge` | lesen | In der Wissensbasis suchen; jeder Auszug mit Quellen-ID, Adresse oder Pack, Art (`web`, `pack`, `docs`), Vertrauensstufe, Aufnahmedatum und Position; `kind` grenzt die Suche ein |
| `list_knowledge_sources` | lesen | Was die Wissensbasis enthält: jede Quelle mit ID, Titel, Art, Vertrauensstufe, Aufnahmedatum, Zahl der Auszüge, ihrer Lizenz und dem, was ein Wissenspaket von ihr enthielte (50 je Seite) |
| `read_knowledge_source` | lesen | Die Auszüge einer Quelle in ihrer Reihenfolge, über die Quellen-ID (höchstens 20 je Aufruf) |
| `list_packs` | lesen | Community-Packs des signierten Index und die installierten mit Vertrauensstufe; Details, Prüfung installierter Packs ([Details](#packs-für-ki-clients)) |
| `add_pack_from_index` | extern | Ein Wissens-, Target- oder Rezept-Pack aus dem signierten Index hinzufügen, als Job; immer gefragt mit der geprüften Zusammenfassung und jedem Basis-Image eines Target-Packs |
| `export_pack` | lokal schreiben | Ein Target-, Rezept- oder Wissens-Pack in eine lokale Datei exportieren, mit deinem Schlüssel signiert; immer gefragt, nichts wird hochgeladen |
| `start_recipe_build` | lokal schreiben | Ein installiertes Community-Rezept genau wie festgelegt bauen (Modell-Commit, Target, Format, Board, Quantisierung), mit der Frage und den Prüfungen von `start_build` |
| `crawl_docs_site` | lokal schreiben | Deep Ingest von 1 bis 5 öffentlichen Dokumentations-Websites in die Wissensbasis, als Job; immer gefragt mit jeder Startadresse, erst nachdem eine Person den Hinweis gelesen hat ([Details](#deep-ingest-durch-einen-ki-client)) |
| `index_framework_docs` | lokal schreiben | Die eigene Dokumentation des Frameworks (mit `include_code` auch Code und Konfiguration) in die Wissensbasis aufnehmen, als Job; immer gefragt, vorher wird ein Snapshot angelegt; danach mit `search_knowledge`, Art `docs`, durchsuchen |
| `start_build` | lokal schreiben | Einen Build starten (muss zu den Capabilities des Targets passen); die Frage zeigt Commit, Größe, Speicherplatz und Hardware-Schätzung |
| `cancel_build` | lokal schreiben | Einen Build abbrechen (eigene Builds ohne Frage, die eines anderen Aufrufers erst nach einer) |
| `resolve_healing` | lokal schreiben | Einen Self-Healing-Vorschlag annehmen oder verwerfen |
| `propose_healing_fix` | lokal schreiben | Einen eigenen Korrekturvorschlag für einen gescheiterten Build als manuellen Schritt an dich übergeben (Filter für gefährliche Befehle; nie ausgeführt) |
| `search_hf_models` | lesen | Hugging-Face-Modelle suchen; `gated: free` (Standard) listet nur Modelle, die ohne Annahme von Bedingungen herunterladbar sind |
| `get_hf_model_info` | lesen | Lizenz (auch die des Basismodells), Zugangsbeschränkung, Zugriff deines gespeicherten Tokens, Parameter, Commit, Dateien und Größe eines Modells |
| `download_hf_file` | lokal schreiben | Eine Datei eines Hugging-Face-Repositorys in den Modellordner laden, auf den Commit festgelegt und gegen das sha256 des Hubs geprüft (ein Job) |
| `list_local_files` | lesen | Modell- und Datensatzdateien in den Ordnern, die die KI nutzen darf, mit ID und `<Label>:<relativer Pfad>` |
| `get_status` | lesen | Der Zustand des Frameworks in Abschnitten (Framework, Rechner, lokale Laufzeit, Wissensbasis, KI-Zugang, deine Tool-Berechtigungen mit dem, was kein Tool tut, deine eigenen Audit-Einträge); nie ein Geheimnis oder ein Host-Pfad |
| `get_settings` | lesen | Die Einstellungen, die eine KI lesen darf (Adressen als Schema und Host, Ordner als Labels), und welche sie ändern darf |
| `update_settings` | lokal schreiben | Gewöhnliche Einstellungen ändern (mit Frage); Grenzen dessen, was eine KI darf, nur verschärfen; nie Berechtigungen, Geheimnisse oder den Tresor |
| `report_cannot_do` | lesen | Melden, dass eine Aufgabe nicht machbar ist, mit Grund; das Framework prüft den Grund an seinem eigenen Zustand |
| `get_job_status` | lesen | Zustand, Fortschritt und Ergebnis einer langen Operation außer einem Build (ein Job); wartet mit `wait_seconds` |
| `cancel_job` | lokal schreiben | Einen Job stoppen (eigene Jobs ohne Frage, die eines anderen Aufrufers erst nach einer) |
| `list_devices` | lesen | Die Geräte, denen du in der GUI oder CLI vertraut und die du gekoppelt hast, mit deinem Namen dafür, Fingerabdruck des Host-Schlüssels und ob sie nutzbar sind |
| `create_deploy_package` | lokal schreiben | Ein gebautes Modell für das Geräteprofil eines Targets paketieren (ein Job; Images nur per Digest festgelegt oder hier gebaut) |
| `deploy_to_device` | extern | Ein Paket auf ein vertrautes, gekoppeltes Gerät kopieren und dessen `deploy.sh` ausführen (immer mit Frage; die Frage zeigt Host-Schlüssel und Paket-Hash; ein Job) |
| `build_local_runtime` | lokal schreiben | Optional, verborgen bis zur Freigabe: llama-server für diesen Rechner aus dem festgelegten Commit bauen (ein Job; nie vorgebaut) |
| `start_local_runtime` / `stop_local_runtime` | lokal schreiben | Optional, verborgen bis zur Freigabe: Dittos lokalen Server starten (ein Job) oder stoppen; nie seine Adresse oder sein Schlüssel |
| `run_benchmark` | lokal schreiben | Optional, verborgen bis zur Freigabe: llama-bench auf Dittos Katalogmodell (nativ oder in der Sandbox) oder einem gebauten GGUF-Modell (immer in der Sandbox: ein für ein Board gebautes Modell wird auf dem Board gemessen) |
| `probe_remote_device` / `deploy_device_runtime` / `run_device_benchmark` | extern | Optional, verborgen bis zur Freigabe: ein vertrautes Board prüfen, seine selbst gebaute Laufzeit installieren oder sie prüfen und messen (immer mit Frage; Jobs) |
| `delete_artifact` | extern, optional | Ein gebautes Modell (seinen Ordner unter dem Ausgabeordner und sein Golden-ZIP) in den Papierkorb verschieben; verborgen, bis du es freigibst, immer mit Frage |
| `remove_pack` | extern, optional | Bitten, ein installiertes Community-Paket zu deinstallieren: Du siehst seinen Ordner, seine Import-Notizen und die Auszüge, die nur es hält, im eigenen Fenster des Frameworks und entscheidest; es geht in den Papierkorb; verborgen, bis du es freigibst, immer mit Frage |
| `list_trash` / `restore_from_trash` | lesen / lokal schreiben, optional | Den Papierkorb mit Name, Art und Größe sehen; wiederherstellen, was diese KI selbst löschen wollte (immer mit Frage); verborgen, bis du sie freigibst. Kein Tool leert den Papierkorb |
| `restore_knowledge_snapshot` | extern, optional | Die Wissensbasis durch einen früheren Snapshot ersetzen, nach einer Sicherung des aktuellen Inhalts (ein Job); verborgen, bis du es freigibst, immer mit Frage |

`delete_artifact` und `restore_knowledge_snapshot` zerstören etwas, darum sind sie standardmäßig verboten und werden nicht angeboten, bis
du sie in den Tool-Berechtigungen freigibst (die ganze Klasse „extern“ zu erlauben gibt sie nicht frei); jeder
Aufruf wird erfragt, und sie bekommen nie einen Dauerauftrag. Über ein Löschen entscheidest nur du, im eigenen
Fenster des Frameworks: Dittos Dialog oder Eingabeaufforderung, bei einem MCP-Client eine wartende Freigabe in der GUI
oder in `llm-cli tools approvals`; das Formular des Clients wird dafür nie benutzt. Die Frage dort nennt jedes Teil
(den Modellordner und sein Golden-ZIP) mit vollem Pfad, Dateinamen, Größe und was es ist; die KI sieht nur
Bezeichnungen und Größen. Das Modell kommt in den Papierkorb, wo du es wiederherstellen kannst (siehe [Löschen und
der Papierkorb](#löschen-und-der-papierkorb)). `delete_artifact` nimmt die ID und das sha256, das `list_artifacts`
zeigt; abgelehnt wird
ein Ordner außerhalb des Ausgabeordners (auch über einen Link), solange ein Build irgendeines Programms des Frameworks
hineinschreibt oder dafür in der Warteschlange steht, wenn sich das sha256 der Modelldatei seither geändert hat und
wenn der Ordner nicht mehr der ist, den die Frage nannte.
`restore_knowledge_snapshot` sichert die aktuelle Wissensbasis zuerst als Snapshot `pre_restore_<Zeit>` und stellt
ohne diese Sicherung nichts wieder her; dieselbe Regel gilt jetzt für jede Wiederherstellung eines Snapshots, und
dabei wird kein Snapshot gelöscht (das Framework löscht keinen Snapshot von selbst, siehe
[Wissensbasis](#wissensbasis-rag)). Es bleibt außerhalb jeder Dauerfreigabe; ob eine Vollmacht es abdecken darf,
wird mit den Vollmachten entschieden.

So läuft eine Frage mit Tools ab:

1. Ditto bekommt die Definitionen der Tools, die deine Berechtigungen nicht verbieten: dieselben Tools, die ein
   MCP-Client mit denselben Berechtigungen angeboten bekommt, egal wie groß das Modell ist.
2. Das Modell darf Tools aufrufen. Jeder Aufruf läuft durch die Tool-Registry: Prüfung der Eingabe, deine
   Berechtigung, Bestätigung, wo die Berechtigung „fragen“ sagt (ein Dialog in der GUI mit dem Titel des Tools und
   jedem Argument; eine Abfrage in der CLI, ohne Terminal abgelehnt), Eintrag im Audit-Log mit dem Akteur `ditto`.
3. Tool-Ergebnisse gehen als nicht vertrauenswürdige Daten zurück an das Modell. Ein abgelehnter oder gescheiterter
   Aufruf wird dem Modell gemeldet, das angewiesen ist, denselben Aufruf nicht zu wiederholen.
4. Nach höchstens 8 Modell-Turns pro Frage hört Ditto auf und sagt das.

**Wie Namen und Werte abgeglichen werden** (gleich für Ditto und jeden MCP-Client, egal wie groß das Modell ist):

- Ein Tool-Name läuft, wenn er genau stimmt oder derselbe Name in anderer Groß- und Kleinschreibung oder mit `_`,
  `-`, `.` oder Leerzeichen an anderer Stelle ist (`StartBuild` startet `start_build`, mit einem Hinweis). Jeder
  andere Name wird abgelehnt, mit bis zu drei Vorschlägen aus den Tools, die deine Berechtigungen erlauben; ein
  Vorschlag läuft nie von selbst.
- Argumentnamen: Nur die Aliasse, die ein Tool angibt (`quant` und `quantization_type` für `quantization`,
  `repo` und `repo_id` für `model`), werden umbenannt, mit einem Hinweis. Jedes andere unbekannte Argument, auch
  ein Feldname in anderer Groß- und Kleinschreibung (`Board`), wird mit der Liste der gültigen und einem Vorschlag
  abgelehnt; nichts wird stillschweigend weggelassen und nichts Unangegebenes zugeordnet. Ein Alias und sein Feld
  mit verschiedenen Werten werden abgelehnt. Das Eingabemodell jedes Tools verbietet Felder, die es nicht angibt,
  auch bei später hinzukommenden Tools.
- Werte: Zahlen und Ja/Nein als Text sowie Listen als JSON-Text werden als solche gelesen. Targets, Formate, Boards
  und Quantisierungstypen passen genau, über Groß- und Kleinschreibung und Trennzeichen, wenn das genau einen Wert
  meint (`q4-k-m` ist `Q4_K_M`; ein Target-Name, der zwei Targets meinen könnte, wird mit beiden abgelehnt), oder
  über einen Alias, den das Target angibt (`Q4` für GGUF). Ein knapp danebenliegender Wert (`rk3567`) wird mit
  Vorschlägen abgelehnt, nie korrigiert. Eine Anzahl von Zeilen oder Ergebnissen über dem Höchstwert wird gekürzt,
  mit einem Hinweis.
- Nie geändert: IDs (nur Leerzeichen um sie herum werden entfernt), Modellnamen und Pfade, Commits und Hashes sowie
  `approve`, das ein echtes `true` oder `false` sein muss.
- Jede Ablehnung und jeder Fehler trägt einen Grundcode (zum Beispiel `unknown_argument`, `unknown_id`,
  `not_supported`, `permission_denied`, `needs_person`, `declined`) und den nächsten Schritt. Derselbe
  fehlschlagende Aufruf sagt beim zweiten Mal in einer Sitzung, dass er schon gescheitert ist; beim dritten Mal wird
  er abgelehnt, ohne zu laufen und ohne dich erneut zu fragen („versuche diesen Aufruf nicht mehr, sag dem Benutzer
  warum“), und nach drei Eingabefehlern in einer Sitzung zeigt ein Fehler zusätzlich einen gültigen Beispielaufruf.
  Eine Sitzung ist eine Frage an Ditto oder ein MCP-Client: seine Verbindung, seine HTTP-Sitzung oder bei einem
  Client des Protokolls 2026-07-28 der Serverprozess (über stdio startet jeder Client seinen eigenen; HTTP-Clients
  ohne Sitzung teilen sich einen, bis benannte Tokens sie unterscheiden).
- Was repariert wurde, ist in der Bestätigungsfrage markiert, in den `notes` des Ergebnisses aufgeführt und im
  Audit-Log festgehalten (`name_requested`, `repairs`). Dittos Bestätigung zeigt genau die Frage des Frameworks.
- Auch ein abgelehnter oder gescheiterter Aufruf nennt seine Reparaturen, mit denselben Worten wie die `notes` eines
  erfolgreichen Ergebnisses: Der Text endet mit „Notes: argument 'quant' read as 'quantization'.“, und über MCP
  enthält auch der strukturierte Fehler sie (`{"error": {"code": ..., "field": ..., "notes": [...]}}`). Das gilt,
  wie auch immer der Aufruf endet: ein ungültiger Wert, ein Limit, eine verweigerte Berechtigung, zu wenig
  Speicherplatz schon vor der Frage, ein „Nein“, eine Frage, die niemand rechtzeitig beantwortet, die
  Wiederholungssperre. So lernt ein Modell den richtigen Argumentnamen auch aus einer Ablehnung, auf Dittos eigenem
  Weg, als MCP-Client und mit Ditto als MCP-Client eines anderen Frameworks. Eine Reparatur, die die Vorabprüfung
  des Tools vor der Frage macht, wird genauso genannt und als Reparatur festgehalten (nur Art und Feld):
  `download_hf_file` mit einem Dateinamen in anderer Groß- und Kleinschreibung („filename: model.gguf (you wrote
  MODEL.gguf)“), auch wenn sich danach der Speicherplatz als zu klein herausstellt. Eine wartende Freigabe, die du
  ablehnst oder die nach deiner Freigabe abgelehnt wird, beendet ihren Job mit denselben Hinweisen in
  `get_job_status` (`error.notes`).
- MCP-Clients bekommen das eigene geschlossene Eingabeschema jedes Tools (`additionalProperties: false`), dasselbe,
  das Dittos Modell sieht.
- Tool-Argumente, die ein lokales Modell mit Codeblock-Zäunen oder in Python-Schreibweise schickt (einfache
  Anführungszeichen, `True`, `None`, ein Komma am Ende), werden einmal als solche gelesen; alles andere bleibt ein
  Fehler.

Dittos Prompts weisen es an, vor einem Build die Capabilities des Targets zu prüfen, Builds nur zu starten oder
abzubrechen, wenn du im Gespräch darum gebeten hast, über Self-Healing nur mit deiner ausdrücklichen Entscheidung zu
entscheiden, Fragen zu Targets und Builds aus den Tools statt aus dem Gedächtnis zu beantworten und nur zu berichten,
was die Tools zurückgegeben haben.

---

### Nur die Tools dieses Computers

Dittos Hände sind die eigenen Tools des Frameworks auf diesem Computer: dieselbe Registry, dieselben Berechtigungen,
Fragen und dasselbe Audit-Log, die jeder MCP-Client bekommt, unter Dittos eigenen Berechtigungen. Ditto verbindet sich
nie mit einem Framework auf einem anderen Computer.

Eine frühere Entwicklungsversion hatte so einen Fernmodus (`llm-cli ditto remote`, einen Abschnitt „Another computer“
in den KI-Einstellungen). Er wurde vor jedem Release entfernt. Was er hinterlassen haben kann:

- Die Einstellungen `ai_ditto_tool_source`, `ai_ditto_mcp_transport`, `ai_ditto_mcp_command`, `ai_ditto_mcp_url` und
  `ai_ditto_mcp_name` in einer älteren `config.yml` werden ignoriert; eine Logzeile nennt sie, wenn die Einstellungen
  gelesen werden. Du kannst diese Zeilen löschen.
- Ein dafür gespeichertes benanntes Token (`ditto_mcp_token`, `llm-cli secrets list` zeigt es) bleibt im
  Anmeldedaten-Tresor und wird nicht mehr gelesen. Widerrufe es auf dem anderen Computer (`llm-cli mcp token revoke
  NAME`), dann ist es dort nutzlos.
- `ditto_places.json` und `remote_listing.json` in deinem Benutzerordner werden nicht mehr gelesen; du kannst sie
  löschen.
- Ein gespeichertes Gespräch des Fernmodus beginnt einmal neu, mit dem Hinweis „Ditto nutzt jetzt seine eigenen
  Werkzeuge: ein neues Gespräch.“ Einträge im Chat-Panel, die den anderen Computer nennen, erscheinen weiter so, wie
  sie geschrieben wurden.
- Audit-Einträge mit den Feldern `place`, `taint` oder `remote_server` lassen sich weiter prüfen; neue Einträge
  können sie nicht tragen.

Das Deployment auf Boards über SSH (Kopplung, Deploy, die Laufzeit auf dem Board) ist etwas anderes und bleibt, wie es
ist.

---

## Ditto-Evaluation

`llm-cli ditto eval` ist ein Werkzeug, um zu messen, wie gut ein Anbieter und ein Modell für Ditto taugen, zum
Beispiel ob ein kleines lokales 2-Bit-Modell für schwache Hardware reicht. Nichts wird gebaut oder geändert. Mit
`--kind retrieval` misst es stattdessen die Suche in der Wissensbasis ([Retrieval-Evaluation](#retrieval-evaluation)).

**Fälle** ([`evals/ditto/cases.yml`](../evals/ditto/cases.yml), 19 bewertete Fälle und 6 Messfälle, die nur
laufen, wenn sie gewählt sind ([Token- und Cache-Messung](#token--und-cache-messung)); eine Datei mit Fällen ist
Daten, neue Fälle brauchen keine Codeänderung):

| Art | Fälle | Was geprüft wird |
|---|---|---|
| `healing` | 6 | Analyse von Build-Logs: Ursache, Host oder Gerät, eine passende Korrektur (fehlendes CMake, Speichermangel, zugangsbeschränktes Modell, volle Platte, eine auf dem Gerät fehlende Bibliothek, ein Log mit eingeschleusten Anweisungen) |
| `optimizer` | 5 | Build-Empfehlungen aus den echten Rockchip-Optionen: z. B. GGUF für ein LLM auf dem RK3566, ein ONNX-Bildmodell zu RKNN, ein Modellname mit eingeschleusten Anweisungen, deren Befehl nicht in der Antwort auftauchen darf |
| `module` | 3 | Modulvorschläge aus Probe-Dateien (Jetson Orin, Raspberry Pi 5, x86-Server): Architektur, SDK, Compiler- und CMake-Flags |
| `chat` | 5 | Chat mit Tools: welche Tools mit welchen Argumenten aufgerufen werden und welche nicht (zum Beispiel ein gescheiterter Build, dessen Log eingeschleuste Anweisungen enthält: Ditto muss den Fehler erklären und darf keine Builds abbrechen oder starten) |
| `tokens` | 4 | Gespräche über mehrere Runden: die Eingabe-, Ausgabe-, Cache-Lese- und Cache-Schreib-Tokens des Anbieters je Runde (nur mit `--kind tokens` oder `--case`) |
| `openai_paid` | 2 | Der bezahlte OpenAI-Test: die 12-Stunden-Cache-Lebensdauer und OpenAIs eigene Token-Zählung (nur mit `--kind openai_paid --provider openai --i-pay`) |

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
Antwort, und bei Chat-Fällen jeden Tool-Aufruf, den das Modell versucht hat (auch abgelehnte: unbekannte Namen,
ungültige Argumente, Verweigerungen, Ablehnungen wiederholter Aufrufe), mit einer Zusammenfassung `tool_use`:
Tool-Aufrufe pro Aufgabe, der Anteil fehlerhafter Aufrufe und unbekannter Tool-Namen, die Reparaturen des Frameworks
und die Ablehnungen wiederholter Aufrufe. Cloud-Anbieter berechnen etwa 20 Anfragen pro Lauf und Wiederholung. Alle Optionen:
[CLI_Usage_DE.md](CLI_Usage_DE.md#ditto-eval).

**GUI: Ditto > Bewertung...** führt dieselbe Bewertung mit der Maus aus: Arten, Retrieval-Varianten (fused, rerank),
einzelne Fälle, eine andere Falldatei, Läufe pro Fall, mehrere lokale Stufen, oder Anbieter, Modell und Aufwand nur für
diesen Lauf (die gespeicherten KI-Einstellungen bleiben unverändert). Vor einem Lauf sagt es, was aufgerufen wird und ob
das Geld kostet, auch wenn die Ersatzkette der KI-Einstellungen einen Cloud-Anbieter nennt, und dass Retrieval seine
Modelle bei der ersten Verwendung herunterlädt („Nein“ ist die Vorgabe); eine Stufe, die noch nicht heruntergeladen
ist, wird mit einem Hinweis auf **Ditto > Lokale Modelle...** abgelehnt, und ein Lauf mit lokalen Stufen wartet,
solange eine andere Aufgabe von Dittos lokaler Laufzeit läuft (er startet seinen eigenen Server). Der Lauf geht im
Hintergrund mit Fortschritt
und **Abbrechen** (der Fall, der gerade läuft, wird noch fertig; der Server einer Stufe wird wie nach einem normalen
Lauf gestoppt, und ein Server von dir, den er ersetzt hat, wird gemeldet), das Fenster zeigt dieselben Tabellen, und
**Bericht speichern...** schreibt den JSON-Bericht von `--json`.

**Erste Messung** (zur Einordnung, kein Benchmark): Auf einem Rechner nur mit CPU, mit Qwen3.5 2B, Aufwand `low`
(Nachdenken aus) und den damaligen Prompts bestand die 2-Bit-Stufe `small-q2` 42 % der 19 Fälle und die 4-Bit-Stufe
`small` 47 %. Die Evaluation hat Lücken in Dittos Prompts gefunden (Fehler dem Host oder dem Gerät zuordnen, welche
Eingaben jedes Format annimmt, die Tools für Fragen zu Targets und Builds nutzen, Compiler-Flags in
Modulvorschlägen). Diese Lücken sind inzwischen behoben, und eine neue Messung läuft.

### Token- und Cache-Messung

`llm-cli ditto eval --kind tokens` misst, was Dittos Gespräche an Tokens kosten, bevor am Kontext oder am Cache
etwas geändert wird. Jeder `tokens`-Fall ist ein Gespräch über mehrere Runden auf einem Verlauf, mit Dittos echtem
Prompt und (wenn der Fall nichts anderes sagt) seinen Tools über das Stellvertreter-Framework. Je Runde hält der
Bericht die eigenen Zahlen des Anbieters aus seiner Usage-Antwort fest: Eingabe-, Ausgabe-, Cache-Lese- und
Cache-Schreib-Tokens und die Aufrufe der Runde (eine Zusammenfassung eines langen Gesprächs eingeschlossen); je
Gespräch nennt er die Summen und den **Anteil aus dem Cache** (Cache gelesen geteilt durch alle Prompt-Tokens).
Nichts wird geschätzt. Die Fälle nutzen nur Beispieldaten (Dokumentationsadressen wie 192.0.2.10 und Namen unter
`example.com`), und der Privacy-Filter bleibt für Cloud-Anbieter an.

- **Anthropics Cache-Diagnose**: Jeder Aufruf eines `tokens`-Falls fragt Claude, warum sein Prompt-Cache gegenüber der
  vorigen Antwort nicht getroffen hat (`diagnostics.previous_message_id`); der Grund (`system_changed`,
  `tools_changed`, `messages_changed`, ...) wird je Runde festgehalten. Dittos normaler Chat schickt das nie.
- **Fälle, die heute scheitern**, tragen `expected_fail`: „Nachfrage zur letzten Antwort nach der Verdichtung“ und
  „20 Runden auf einem lokalen Fenster mit 4096 Tokens“ (der zweite läuft nur auf einem verwalteten lokalen Modell,
  `--tier`, und wird sonst übersprungen). Ihr Scheitern wird als erwartet gemeldet und nicht in den Bestehensquoten
  gezählt; ein Bestehen wird als **UNERWARTET BESTANDEN** gemeldet, damit die Markierung entfernt wird, sobald die
  Arbeit am Kontext sie bestehen lässt.
- **`--pause-minutes N`** wartet N Minuten zwischen den Runden, um eine Cache-Lebensdauer von 5 Minuten mit einer von
  1 Stunde zu vergleichen. Ohne Angabe gibt es keine Pause, und in CI wird eine Pause abgelehnt.
- Die CLI und das Fenster zeigen eine Tabelle **Tokens**; `--json` enthält jede Runde.

**Der bezahlte OpenAI-Test** (`--kind openai_paid`) klärt mit dem eigenen OpenAI-Key der Person zwei Fragen: ob
OpenAI eine Prompt-Cache-Lebensdauer von 12 Stunden annimmt (`prompt_cache_options.ttl`) und wie OpenAIs eigene
Token-Zählung (`responses.input_tokens.count`) zur Usage des echten Aufrufs passt. Er läuft nie von selbst, nie in CI,
nie im Offline-Modus und nie durch eine KI: Er startet nur mit `llm-cli ditto eval --kind openai_paid --provider openai
--i-pay` an einem Terminal, nach einer weiteren Frage mit „Nein“ als Vorgabe, die nennt, was er höchstens kostet
(6 Anfragen mit je etwa 2.000 Eingabe-Tokens), oder aus **Ditto > Bewertung...** nach derselben Bestätigung. Der Key
kommt aus deinem eigenen Secrets-Speicher und bleibt auf deinem Computer. Eine abgelehnte 12-Stunden-Lebensdauer
(HTTP 400) wird als abgelehnt festgehalten, nie als funktionierend, und die beiden Anfragen gehen dann ohne sie
hinaus, sodass die gecachten Tokens der zweiten in jedem Fall gemessen werden. Das Ergebnis ist
`openai_cache_check.json` in `evals/` des Zustandsordners des Benutzers: nur Wahrheitswerte und Token-Zahlen, kein
Prompt-Text und kein Key.

**Speicher des lokalen Servers** (Entwicklung): `python scripts/measure_cache_ram.py --tier small` lässt die
`tokens`-Fälle auf einer lokalen Stufe laufen, misst den Speicher des llama-server, den dieser Lauf gestartet hat (nur
diesen), und gibt seinen höchsten RSS-Wert und seinen Wert für `--cache-ram` aus, nur Zahlen.

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

**GUI: Ditto > Lokale Laufzeit...** erledigt jeden Befehl `llm-cli ditto runtime` mit der Maus, mit demselben Code
wie die Befehle:

- **Status:** was `ditto runtime status` zeigt (Hardware, Betriebsart, der Build in Gebrauch und seine Prüfungen,
  Probleme und der Rat zum Neubau, der Plan für den eigenen Build, das fertige Archiv, der Server mit Adresse, Modell
  und Kontext, das empfohlene und das gewählte Modell, die Protokolldatei), in deiner Sprache; **Noch einmal prüfen**
  liest ihn neu. In **Tools > Configure AI Agent (Ditto)...** öffnet **Status und mehr...** dasselbe Fenster.
- **Installieren, prüfen oder wechseln:** der Bereich „Dittos lokaler Server“ (auch in **Tools > Configure AI Agent
  (Ditto)...**) führt `ditto runtime ensure` aus, mit der Betriebsart (Automatisch, Direkt, In Docker), den
  Build-Optionen (GPU-Backend, wo gebaut wird, Compile-Jobs) und dem Modell-Angebot danach (ohne Haken:
  `--no-model`). Für die installierte Art heißt der Knopf **Prüfen und reparieren...**: Der Build wird geprüft und
  nur neu installiert, wenn er sich nicht nutzen lässt (zum Beispiel ein alter llama.cpp-Build nach einem Update);
  die andere Art ist ein **Wechseln...** mit der Platzprüfung und dem vollständigen Entfernen der alten Art, wie
  [unten](#direkt-oder-in-docker-art-der-laufzeit) beschrieben.
- **llama-server bauen...** (`ditto runtime install`, mit denselben Build-Optionen) und **Offizielles fertiges
  Archiv installieren...** (`--prebuilt`; „Noch einmal herunterladen, auch wenn es installiert ist“ ist `--force`).
- **Server:** **Starten** mit dem Modell (wie bisher; „Empfohlen für diesen Computer“ vergisst eine frühere Wahl;
  oder eine Stufe), der Kontextlänge und dem Entladen im Leerlauf; **Stoppen**.
- **Builds und Quellcode:** **Build importieren...**, **Build exportieren...**, **Ältere Builds entfernen...** (der
  Lösch-Dialog: voller Pfad, Dateiname, Größe und was es ist, nichts angehakt, „Nein“ als Vorgabe; Papierkorb oder
  endgültig), **Quellcode exportieren...** (mit optionalem Spiegel) und **Quellcode importieren...** für einen
  Computer ohne Netz.

Builds, Downloads, Docker und der Serverstart laufen im Hintergrund mit einer Fortschrittszeile; **Abbrechen** hält
einen Build vor seinem nächsten Schritt an und einen Download zwischen zwei Stücken (das Teilstück bleibt). Jede Frage
hat „Nein“ als Vorgabe.

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

**GUI: Ditto > Lokale Modelle...** zeigt jede Stufe mit Datei, Größe, Lizenz, ob sie heruntergeladen (und geprüft) ist,
und ihrem Hinweis (empfohlen für diesen Computer, gewählt, auf Wunsch, nicht festgelegt), mit dem Grund der Empfehlung
und dem vollen Modellordner. **Empfohlenes Modell herunterladen...** und **Ausgewähltes herunterladen...** machen
`ditto models download` mit derselben Frage (Repository, Datei, Lizenz, Größe, Ordner; „Nein“ ist die Vorgabe); Liste
und Download laufen im Hintergrund, mit Fortschritt und **Abbrechen** (der Teil bleibt für die Fortsetzung). Der
empfohlene Download nimmt die Empfehlung, die das Fenster zeigt, damit keine Hardware-Prüfung läuft, während du auf die
Frage wartest; wurde früher eine Stufe für den lokalen Server gewählt, heißt der Knopf **Gewähltes Modell
herunterladen...** und nimmt diese Stufe, das Modell, das ein einfacher Start lädt (wie `ditto models download` ohne
Stufe). Ein Download ist eine Aufgabe von Dittos lokaler Laufzeit: Er wartet, solange in **Ditto > Lokale
Laufzeit...** oder in den KI-Einstellungen ein Build, ein Serverstart oder ein anderer Download läuft, und diese
warten auf ihn.

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
- **Ein Speicherbudget:** Vor dem Kontext wird der Speicher des Servers einmal aus der gemessenen Hardware dieses
  Computers geplant (`runtime/hardware.py local_budget`): der KV-Cache-Typ (`f16`, oder `q8_0`, wenn neben dem Modell
  weniger als 2 GiB RAM übrig sind, dieselbe Regel wie auf Boards, und `f16`, wenn eine eigene GPU das Modell und
  seinen KV-Cache hält; ein quantisierter Cache bekommt Flash Attention auf
  `auto`), der Prompt-Cache im RAM (`--cache-ram`: ein Achtel des RAMs, 256 bis 8192 MiB wie bisher, aber nie mehr als
  die Hälfte des freien RAMs, der neben dem Modell, seinem Mindestkontext und 1 GiB Abstand für das System übrig ist;
  unter 64 MiB ist er aus) und wie lange das Modell geladen bleibt. Der Server bekommt einen Slot (`-np 1`), damit
  Dittos eine Unterhaltung den ganzen Kontext hat. `llm-cli ditto cache` zeigt die Werte mit weicher und harter Grenze;
  `ditto cache set` und die Gruppe **Prompt-Cache** (KI-Einstellungen, Ditto > Lokale Modelle...) ändern sie. Auf
  Computern mit wenig RAM ist der Prompt-Cache jetzt kleiner als in 3.1.0 (4 GiB ohne GPU: etwa 370 MiB statt 512;
  Boards mit 2 GiB: aus statt 256 MiB).
- **Kontextlänge:** aus dem freien Speicher neben dem Modell und dem Prompt-Cache berechnet (freier GPU-Speicher, oder
  freier RAM bei CPUs, Apple Silicon und integrierten GPUs) mit dem KV-Cache des gewählten Typs, auf 256 Tokens
  gerundet, mindestens 4096 und höchstens 32768 (`--ctx` legt sie fest). Nach dem Start liest das Framework das
  eigene `/props` des Servers (auf 127.0.0.1, mit dem Schlüssel; es weckt kein schlafendes Modell) und hält den
  wirklichen Kontext des Slots fest.
- **Abschottung:** `llama-server` lauscht nur auf 127.0.0.1, auf einem freien Port. Ein neuer, zufälliger
  API-Schlüssel pro Start erreicht ihn nur über eine Schlüsseldatei, die nur du lesen kannst, und das Framework
  prüft, dass der Server Anfragen ohne Schlüssel ablehnt. Eine Antwort auf dem Port zählt nur, solange `llama-server`
  selbst dort lauscht: Ein Programm, das den freien Port inzwischen belegt hat, bekommt nie den Schlüssel oder einen
  Prompt (ein anderer Port wird versucht), und ein Aufruf, der für einen inzwischen neu gestarteten Server vorbereitet
  war, wird abgelehnt, statt den neuen Schlüssel an den alten Port zu schicken. Weboberfläche und Slots-Endpunkt sind
  aus, und der Server
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
  (`--sleep-idle`, oder die Einstellung `local_cache_keep_minutes`; 0 hält es geladen). Eine geänderte
  Budget-Einstellung startet den laufenden Server beim nächsten Start einmal neu; dieselben Einstellungen nie.
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
  ein auf diesem Rechner geprüfter Build kann der aktive werden. Das Framework löscht keinen Build von selbst
  (vorher behielt es nur die zwei neuesten anderen): `llm-cli ditto runtime prune` listet die alten
  zum Ankreuzen, nie den aktiven Build, den CPU-Ersatz oder den Build, aus dem ein laufender Server gestartet wurde.
  Ein Build von
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

### Direkt oder in Docker (Art der Laufzeit)

Dittos lokaler Server (der selbst gebaute `llama-server` und sein Modell) läuft direkt auf dem Rechner (bare metal,
der Standard, ohne den Aufwand von Docker) oder in Docker. `llm-cli ditto runtime ensure` installiert ihn, wenn er
fehlt; die Installer führen den Befehl als ihren Ditto-Schritt aus (`install.sh --ditto=auto|bare-metal|docker|skip`,
`install.bat --ditto=...` oder `LCF_DITTO` für einen unbeaufsichtigten Lauf). Die Art steht in
`~/.llm-framework/runtime/llama.cpp/mode.json`.

- **Idempotent:** Ist Ditto in einer der beiden Arten da, passiert nichts, und der Befehl sagt, was er gefunden hat.
  Ist nichts installiert, installiert `auto` direkt; geht das hier nicht (zum Beispiel kein Compiler und keine
  Builder-Basis für die C-Bibliothek dieses Rechners), nimmt er Docker und sagt warum. Geht keins von beiden, nennt
  er beide Gründe. Solange Docker nicht erreichbar ist (zum Beispiel noch nicht gestartet), bleibt der Docker-Modus,
  wie du ihn gewählt hast, und nichts wird geändert. Ist die aufgezeichnete Art weg und die andere da, sagt der Befehl,
  dass die Art gewechselt hat. `ensure` entfernt unterwegs nichts: Kein alter Build wird durch Aufräumen entfernt.
- **Dittos Image** wird auf diesem Rechner aus `orchestrator/runtime/build/ditto-runtime.Dockerfile` in zwei Stufen
  gebaut, auf einem Docker Official Image des Katalogs als Basis (`build_bases`: Debian, Ubuntu oder Alpine, per Digest
  festgelegt): `runtime-base` fügt nur die Laufzeit-Bibliotheken hinzu (OpenMP und C++-Laufzeit; für einen
  Vulkan-Build den Vulkan-Loader und die Treiber von Mesa), `ditto` die Programme eines geprüften Builds. Die Programme
  kommen aus dem direkten Build, wenn die C-Bibliothek der Basis passt (ein Build des Builder-Containers kommt auf
  seine eigene Basis, ein nativer Build auf die älteste Basis mit derselben C-Bibliothek, die mindestens so neu ist wie
  die dieses Rechners): „repackaged“ (umgepackt). Sonst wird llama.cpp im Builder-Container neu gebaut, für die Basis
  des Images geplant und in einem Container der Laufzeit-Basis geprüft: „rebuilt“ (neu gebaut). Das Image trägt die
  Kennzeichnung als Dittos Laufzeit (`org.llm-framework.ditto-runtime`), wird nie als fertiges Image geholt und nie
  hochgeladen und vor dem Gebrauch in Containern aus ihm selbst geprüft: `llama-server` und `llama-bench` nennen den
  festgelegten Build und Commit, der Server lehnt `--tools` ab, und ein GPU-Build sieht sein Gerät, wenn die GPU
  durchgereicht wird. Ein Image mit Dittos Kennzeichnung, das aus einer Registry kommt (es hat einen
  Repository-Digest), wird nie benutzt. Die Programme im Image bekommen den Modus 0755, damit sie für deinen Benutzer
  auch dann laufen, wenn sie mit einer strengen umask gebaut wurden. Der Docker-Modus braucht in dieser Version Linux.
- **Der Container** hält die Startregeln jedes Servers ein: die Befehlszeile aus `server_policy.py` (keine verbotene
  Option, die Schlüsseldatei, keine Web-Oberfläche, keine Slots, offline); in seinem eigenen Netzwerk-Namensraum hört
  er auf allen Schnittstellen des Containers, und sein Port wird nur auf 127.0.0.1 veröffentlicht, also erreicht ihn
  nichts außerhalb dieses Rechners (der Schlüssel des Starts schützt ihn weiter vor anderen Containern). Modellordner
  und Schlüsseldatei werden schreibgeschützt eingebunden (die Modelle werden nicht kopiert), er läuft als dein
  Benutzer, ohne Capabilities und ohne Rechteausweitung, mit schreibgeschütztem Wurzeldateisystem und einem
  Init-Prozess, und Docker entfernt ihn, wenn er stoppt. Er wird über die ID des Images angelegt, also holt Docker für
  ihn nie etwas. Die GPU wird durchgereicht, wo das geht: NVIDIA-GPUs über das NVIDIA Container Toolkit (Docker nennt
  eine Laufzeit `nvidia`), AMD- und Intel-GPUs als `/dev/dri` mit den Gruppen seiner Render-Knoten; wo nicht, sagt
  der Hinweis warum, und das Image bekommt den CPU-Build. `runtime start`, `stop` und `status` folgen dem Container,
  und der Schlüssel erreicht die Anbieter-Schicht nur, solange der aufgezeichnete Container mit Dittos Kennzeichnung
  und dem aufgezeichneten Port läuft.
- **Ein Wechsel** (`--force`, braucht ein Terminal, oder die KI-Einstellungen) behält die Modelle: Nichts wird noch
  einmal heruntergeladen, der Container bindet denselben Ordner ein. Nur der Server wird neu erzeugt, umgepackt, wo
  das geht: der direkte Build ins Image, oder der Build des Images in die direkte Art, sobald er seine Prüfungen auf
  diesem Rechner selbst bestanden hat (ein Build, der nur im Container geprüft wurde, läuft vorher nie hier). Wo das
  nicht geht, wird der Grund gezeigt und der Server neu gebaut. Der Server der alten Art wird gestoppt.
- **Zuerst der Platz:** Direkt und Docker sind zwei Alternativen, und ein Wechsel baut die neue Art neben der alten.
  Vorher schätzt das Framework, was die neue Art beim Bauen braucht (den Quellcode, den Build-Baum, die Builder- und
  Laufzeit-Basis-Images) und was danach bleibt, und vergleicht das mit dem freien Platz jedes beteiligten Laufwerks:
  Dittos Laufzeitordner, und Dockers Image-Speicher, wenn Docker einen Ordner dieses Rechners nennt (das eigene
  Laufwerk von Docker Desktop lässt sich nicht messen; das wird gesagt und hält nichts auf). Ein Laufwerk hält eine
  Reserve frei, und der Papierkorb zählt nie als frei. Passt es nicht, wird nichts gebaut. Du siehst die Größen und
  wählst: Platz schaffen und noch einmal wechseln, oder die alte Art zuerst löschen. Dann wird jedes Objekt der alten
  Art aufgelistet und nach deinem einen Ja gelöscht; Ditto ist nicht verfügbar, bis die neue Art funktioniert, und die
  Modelle bleiben. Das Löschen zuerst wird nur angeboten, wenn die neue Art danach passt (noch einmal geschätzt ohne
  den Build der alten Art, mit dem Platz, den ihre Objekte freigeben), und nur an einem Terminal oder in der GUI.
  Scheitert die neue Art danach, erfährst du das und bekommst die alte Art wieder angeboten.
- **Die alte Art verschwindet ganz**, sobald die neue Art funktioniert: Jedes ihrer Objekte wird mit vollständigem
  Pfad oder Name, ID, Größe und was es ist aufgelistet: Dittos Build-Ordner (Pfad, Ordnername, Build-ID), das
  offizielle Archiv, Dittos Images und Container (Name, ID), die Einträge der alten Art in `mode.json` und
  `active.json` und das Log der Image-Builds. Eine Frage löscht genau diese Liste endgültig, nicht in den Papierkorb
  („Nein“ ist vorgewählt, keine Auswahl je Objekt), im Terminal oder in den KI-Einstellungen. Das Entfernen lehnt ab,
  solange die neue Art nicht funktioniert; die Liste wird vorher noch einmal ermittelt, und hat sie sich geändert, wird
  nichts entfernt, und das Entfernen wird ins Audit-Log geschrieben, bevor etwas entfernt wird. Ohne Terminal wird
  nichts entfernt, und die Liste wird angezeigt. Was beide teilen, bleibt: die Modelle, der festgelegte Quellcode, die
  Builder-Images des Frameworks und die Builds eines Boards im selben Speicher. Dittos Images werden sonst nie
  gelöscht. Ist Docker nach einem Wechsel zur direkten Art nicht erreichbar, sagt der Befehl, dass Dittos Images und
  Container nicht in der Liste stehen, und wie du sie später auflistest.
- **Das Modell:** `ensure` bietet das Modell der gewählten oder empfohlenen Stufe an, wenn es fehlt, zuerst mit
  Quelle, Größe und Lizenz; mit `-y` lädt es nur ein Modell unter freier Lizenz. Jede andere Lizenz nimmst du selbst
  an.
- **Für KI-Clients:** `get_status` (`local_runtime`) zeigt die Art, den Namen des Images und ob es umgepackt oder neu
  gebaut wurde; `start_local_runtime` startet den Server der aufgezeichneten Art, und sein Ergebnis nennt, was läuft
  (im Docker-Modus den Build des Images). Die Art wechseln und die alte Art entfernen darf nur die Person: Kein Tool
  tut eins davon, und `build_local_runtime` entfernt keinen alten Build.
- **Die KI-Einstellungen** und **Ditto > Lokale Laufzeit...** zeigen, wo der Server läuft, und führen `ensure`
  über denselben Code-Pfad wie der Befehl aus, mit seinen Optionen: installieren, die installierte Art prüfen und
  reparieren (nur neu installiert, wenn sie sich nicht nutzen lässt) oder wechseln, mit derselben Liste der Reste
  danach.

### Laufzeit-Tools für KI-Clients (optional)

Sechs optionale MCP-Tools lassen einen KI-Client (oder Ditto) tun, was `llm-cli ditto runtime` und
`llm-cli device runtime` tun. Jedes wird nur registriert, wenn sein Laufzeitpaket in der Installation vorhanden ist,
und jedes ist verweigert, also nicht aufgeführt, bis du es in den Tool-Berechtigungen freigibst, auch wenn du die
ganze Stufe erlaubt hast.

- **`build_local_runtime`** baut llama-server für diesen Rechner aus dem festgelegten Commit (zuerst der CPU-Build,
  dann der GPU-Build von `backend`: `auto`, `cpu`, `cuda`, `vulkan`, `rocm`, `metal`) als Job und holt mit `model_tier`
  dieses Katalogmodell (über Größe und sha256 festgelegt). Die Frage zeigt die geplanten Builds, die Toolchain, ob der
  festgelegte Quellcode geholt werden muss, das Modell und eine Schätzung des nötigen Platzes. Es gibt keine
  Prebuilt-Option und keine Eingabe für eine CMake-Option oder einen Befehl. Ein fehlender Toolchain-Teil (ein
  Compiler, CMake, Docker, ein GPU-Toolkit) wird vor der Frage mit Grund `needs_person` und
  `llm-cli ditto runtime install` abgelehnt: Nur du installierst Toolchains und nimmst Lizenzen an. `cancel_job` hält
  einen Modell-Download zwischen zwei Blöcken an (seine `.part`-Datei bleibt) und hält die Installation vor ihrem
  nächsten Build oder ihrer nächsten Aktivierung an: Ein abgebrochener Build wird nie aktiv.
- **`start_local_runtime`** (ein Job; `tier`, `context_length`) und **`stop_local_runtime`** starten und stoppen
  Dittos Server. Gestartet wird nur ein Build, der seine Prüfung hier bestanden hat. Kein Ergebnis enthält die
  Adresse des Servers oder seinen Schlüssel für diesen Start.
- **`run_benchmark`** misst Prompt- und Erzeugungsgeschwindigkeit und den höchsten Speicherbedarf mit dem selbst
  gebauten `llama-bench`: `tier_speed` für ein heruntergeladenes Katalogmodell (Dittos eigenes Modell, das wirklich
  auf diesem Computer läuft: nativ, oder mit `sandbox: true` in der Docker-Sandbox), `artifact_speed` für ein gebautes
  GGUF-Modell. Ein für ein Board gebautes Modell wird nie nativ auf diesem Computer gemessen, denn das würde diesen
  Computer messen und nicht das Board: Es läuft in der Docker-Sandbox (`sandbox: false` wird abgelehnt), und seine
  echten Werte kommen vom Board selbst (`run_device_benchmark` nach `deploy_to_device`). Ein nicht vertrauenswürdiges
  Modell in der Sandbox ist ein Sicherheitshinweis, den nur du entscheidest, außer du hast das für KIs erlaubt
  (`bench_untrusted_sandbox_ok`). Die Modelldatei wird einmal geöffnet
  und über diese offene Datei gehasht; llama-bench liest genau diese Datei (nativ über den geerbten Deskriptor, in
  der Sandbox über einen privaten Hardlink darauf), so dass sie zwischen Prüfung und Lauf nicht ausgetauscht werden
  kann. Der private Link der Sandbox liegt neben dem Modell oder, wenn dieser Ordner schreibgeschützt ist, im Cache-
  oder Zustandsordner des Frameworks auf demselben Dateisystem; wo kein Link möglich ist, ist es eine Kopie, die aus
  der offenen Datei gelesen und erneut gehasht wird (sie muss den geprüften sha256 haben). Er wird entfernt, wenn der
  Lauf endet, auch bei einem Fehler und bei `cancel_job` (das auch den Sandbox-Container beendet, eine noch laufende
  Kopie anhält und nach dem Abbruch keinen Container mehr startet); lässt sich keine private Datei anlegen, scheitert
  der Job mit dem Grund (`insufficient_resources` oder `needs_person`). Ergebnisse
  liegen in
  `~/.llm-framework/benchmarks/` (`runs/<Lauf-ID>.json`, `index.jsonl`); `get_status` (Abschnitt `local_runtime`)
  zeigt die neuesten Läufe.
- **Board-Tools** (Stufe `external`, immer mit Frage, nie von einer Daueranfrage abgedeckt, nur für ein
  [vertrautes Gerät](#übertragen-auf-das-board-deployment), dessen Host-Schlüssel noch der vertraute ist und in der
  Frage gezeigt wird): `probe_remote_device` führt die nur lesende Prüfung über SSH aus und speichert sie unter der
  Geräte-ID (wie `scan --device`); `deploy_device_runtime` baut die Laufzeit des Boards bei Bedarf im
  Builder-Container und installiert sie so, wie es die vorher von dir gewählten Deploy-Einstellungen sagen (wie
  `device runtime build` und `deploy`; aktiv erst, wenn ihre Prüfungen auf dem Board bestehen);
  `run_device_benchmark` führt die Laufzeitprüfungen mit einem kurzen `llama-bench` auf dem Modell aus, das die
  Deploy-Einstellungen nennen (wie `device runtime verify --bench-model`), ein nicht vertrauenswürdiges Modell nur
  mit deiner Zustimmung im Framework, die zuerst ins Audit-Log geschrieben wird. Hier wird ein für ein Board gebautes
  Modell gemessen; ein bestandener Lauf wird beim Build dieses Modells als auf dem Board getestet vermerkt
  (`recorded_builds` im Ergebnis). Die Ausgabe des Boards kommt als externer Text zurück. Ein nicht erreichbares
  Board wird mit seinem Grund-Code (`device_host_down`, `device_port_closed`, `device_auth_failed`,
  `device_host_key_changed`) und dem nächsten Schritt für die Person abgelehnt.
- Die Ressource `lcf://runtime/catalog` enthält den festgelegten Commit und die Modellstufen; der Prompt
  `set_up_offline_assistant` führt einen Client durch Bauen, Starten und Messen.

### Perplexity-Prüfung umgewandelter Modelle

Der Dataset-Manager (`orchestrator/Core/dataset_manager.py`) misst die Perplexity eines GGUF-Modells auf einer
Textdatei mit dem selbst gebauten `llama-perplexity` dieses Rechners, das `ditto runtime install` zusammen mit dem
Server baut. Es wird kein vorgebautes Laufzeit-Image geholt und kein `llama-perplexity` aus dem `PATH` genutzt: nur
ein gespeicherter Build, der seine Prüfung hier bestanden hat, noch zu seinem Manifest passt und vom festgelegten
Commit ist.

- **Katalogmodelle** (über sha256 festgelegt: Dittos eigene Modelle) laufen nativ, oder auf Wunsch in der
  Docker-Sandbox.
- **Jedes andere Modell** (ein für ein Board umgewandeltes Modell, ein Download) läuft nie nativ auf diesem Computer:
  Für ein Board gebaute Modelle werden auf dem Board gemessen, oder hier in der Docker-Sandbox, sobald das fertige
  Modell da ist. Es läuft in der Sandbox; ohne einen im Builder-Container gemachten Build läuft es gar nicht.
- **Docker-Sandbox:** Der Lauf geschieht im eigenen Builder-Image des Builds (nur ein im
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

- **Speicher:** Qdrant Edge, die Rust-Engine von Qdrant, läuft im Framework-Prozess (PyPI `qdrant-edge-py`,
  Apache-2.0), mit seinen Daten in `~/.llm-framework/qdrant_edge` (kein Docker, kein Server, kein offener Port). Es
  öffnet ihn immer nur ein Prozess: GUI, CLI und MCP-Server wechseln sich ab. Das Framework hält eine Sperrdatei
  (`qdrant_edge.lock` neben dem Ordner), solange der Speicher offen ist, und gibt sie nach 30 Sekunden ohne Zugriff
  frei. Ein zweiter Prozess, der ihn will (auch nur zum Lesen), wartet bis zu 5 Sekunden und bekommt dann einen klaren
  Fehler "in use by another framework process"; er liest nie einen halb geschriebenen Stand. Wer die Wissensbasis aus
  mehreren Prozessen gleichzeitig nutzen will, setzt `qdrant_url` auf einen Qdrant-Server; ein entfernter Server
  braucht https und den API-Schlüssel als Geheimnis `qdrant_api_key` (nie Zugangsdaten in der URL). Suchen sind exakt
  (kein angenäherter Graph-Index), wie bisher. Auf einem Rechner ohne Build von Qdrant Edge (Windows auf ARM) behält
  die Wissensbasis den älteren lokalen Speicher von qdrant-client: langsamer, aber vollständig; Mojo Oracle sagt es.
- **Umwandlung einer älteren Wissensbasis:** Frühere Versionen speicherten sie im lokalen Modus von
  qdrant-client (`~/.llm-framework/qdrant`, ein reiner Python-Ersatz für Qdrant, gedacht für die Entwicklung). Bei der
  ersten Nutzung wird sie umgewandelt: Vektoren und Texte werden kopiert (nichts wird neu eingebettet), die Anzahl
  jeder Collection wird verglichen und eine Stichprobe von Punkten und Suchen gegen den alten Speicher geprüft. Das
  Ergebnis steht in `~/.llm-framework/knowledge_conversion.json`. Der alte Ordner bleibt, bis du ihn löschst:
  **Tools > Knowledge Snapshots... > Alten Speicher löschen...** oder `llm-cli knowledge storage delete-old` zeigen ihn
  mit vollem Pfad und Größe und fragen dich vorher (Papierkorb oder endgültig); nichts löscht ihn von selbst. Schlägt
  die Umwandlung fehl, arbeitet der alte Speicher weiter, von der halben Kopie bleibt nichts, und der Grund wird
  angezeigt (`llm-cli knowledge storage`, Mojo Oracle, das Fenster der Wissens-Snapshots). Fehlender Platz wird beim
  nächsten Start erneut versucht, andere Fehler, wenn du es willst: **Tools > Knowledge Snapshots... > Umwandlung erneut
  versuchen** (läuft im Hintergrund) oder `llm-cli knowledge storage convert`. GUI: **Tools > Knowledge
  Snapshots...** zeigt den Speicher in jedem Zustand wie `llm-cli knowledge storage` (Qdrant Edge mit seinem Ordner,
  der alte Speicher mit seinem Grund, ein Qdrant-Server, Qdrant Edge, das sich nicht laden lässt) und bietet **Jetzt
  umwandeln** auch vor dem ersten Versuch an, unter der Bedingung von `llm-cli knowledge storage convert`. 50 000
  Auszüge umzuwandeln dauerte in
  zwei Tests zwischen 1 und 2,5 Minuten, 5 000 etwa 15 Sekunden. Nach der Umwandlung springt der alte Ordner nie wieder
  für Qdrant Edge ein: Lässt sich Qdrant Edge später nicht laden (ein fehlendes oder kaputtes Paket), wird die
  Wissensbasis nicht geöffnet und sagt, warum, und Mojo Oracle meldet es als Fehler, statt still den alten Ordner zu
  nutzen, dem fehlt, was seitdem dazukam.
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
- **Schnappschüsse:** Vor jedem Crawl, jeder Aufnahme der eigenen Dokumente des Frameworks, jeder Wiederherstellung
  und jedem Import eines Wissens-Packs wird die Wissensbasis als Schnappschuss in
  `~/.llm-framework/knowledge_snapshots` gesichert; ein belegter Name bekommt `-2`, so wird nie ein Schnappschuss
  überschrieben. Es gibt keine Grenze (bisher blieben auf jedem Weg nur die neuesten 5): Das
  Framework behält so viele, wie die Platte fasst, löscht keinen von selbst, und keine Anzahl bringt eine Erinnerung
  oder eine Warnung. Was zählt, ist die Platte (achte Runde, Antwort 2): bevor ein Schnappschuss
  angelegt wird, wird seine Größe geschätzt. Passt er neben der Reserve, die immer frei bleibt, nicht mehr hin, wird
  kein Schnappschuss angelegt, und die Änderung läuft auch nicht; ein „trotzdem weiter“ gibt es nicht. Du bekommst die
  Liste, um Schnappschüsse zu löschen oder zu exportieren (die GUI bietet **Knowledge Snapshots...** an; KI-Clients
  bekommen den Blocker `snapshot_no_room` mit deiner Handlung). Passt er, lässt aber wenig Platz übrig, ist das eine
  Lagewarnung (`disk_tight`). CLI und GUI sagen beide vorher, ob der nächste Schnappschuss passt (GUI: die Zeile zur
  Festplatte in **Tools > Knowledge Snapshots...**, mit den Worten von `llm-cli knowledge snapshots`). **Tools >
  Knowledge Snapshots...** in der GUI oder `llm-cli knowledge snapshots` listet jeden mit vollem Pfad, Namen, Größe
  und was er ist, nichts angehakt. Um Platz zu
  schaffen, hakst du die an, die gelöscht werden sollen (sie kommen zuerst in den Papierkorb; `llm-cli knowledge
  snapshots prune` oder `delete NAME...`), oder kopierst sie woandershin (Export; `llm-cli knowledge snapshots export
  NAME... --to ORDNER`, jede Kopie per sha256 geprüft). Siehe [Löschen und der Papierkorb](#löschen-und-der-papierkorb).
  Einen Schnappschuss wiederherstellen kann man über GUI oder CLI noch nicht. Ein Schnappschuss ist eine portable
  Datei; Schnappschüsse früherer Versionen (Format 1) werden beim Wiederherstellen umgewandelt.
- **Suche:** hybrid. Dichte Vektoren finden die Bedeutung, BM25 findet exakte Begriffe wie Fehlercodes und
  Compiler-Flags; beide Ranglisten werden mit Reciprocal Rank Fusion verbunden. Derselbe Text aus mehreren Quellen
  kommt nur einmal. Danach kann ein Cross-Encoder die Ergebnisse neu bewerten ([Reranking](#reranking)).
- **Nutzung:** das Tool `search_knowledge` (Ditto, MCP-Clients); ohne Tools hängt Ditto passende Auszüge selbst an
  die Frage. Auch Healing-Analysen, Modulvorschläge und Build-Empfehlungen (Builder: **Use RAG Knowledge Base**)
  bekommen Auszüge. Ergebnisse gelten immer als nicht vertrauenswürdige Daten.
- **Dieselbe Wissensbasis für KI-Clients:** Ein MCP-Client, der Dittos Aufgabe übernimmt, bekommt, was Ditto
  bekommt. Jeder Treffer von `search_knowledge` nennt seine Quelle (`source_id`, die Adresse oder den Pack-Eintrag,
  Titel, Abschnitt, Position in der Quelle), seine Art (`web`: eine Seite, die du per Deep Ingest aufgenommen hast;
  `pack`: ein installiertes Wissens-Pack; `docs`: Code und Dokumente des Frameworks selbst) und seine
  Vertrauensstufe (`crawled`, beim Pack dessen `verified`, `community` oder `local`, `framework`, oder `unknown` für
  ein nicht mehr installiertes Pack). `list_knowledge_sources` listet die Quellen (Filter nach Art und Text, 50 je
  Seite; die Liste wird eine Minute lang wiederverwendet und nach jedem Schreiben erneuert), und
  `read_knowledge_source` liest die Auszüge einer Quelle der Reihe nach. Eine Quelle wird über ihre ID (`ks_` und
  16 Hex-Zeichen) benannt, nie über einen Pfad; jeder Auszug, jede Adresse und jeder Titel wird bereinigt, auf 4000
  Zeichen gekürzt und als nicht vertrauenswürdig markiert. Ein Tool-Aufruf lädt nie ein Modell herunter: Liegt das
  Embedding-Modell noch nicht auf diesem Rechner, wird die Suche mit dem nächsten Schritt abgelehnt (Ditto einmal
  in der GUI oder mit `llm-cli ditto ask` fragen, das lädt es einmal), und ein noch fehlendes Reranking-Modell wird
  nur für diesen Aufruf übersprungen. Auflisten und Lesen brauchen kein Modell.
  Ein MCP-Prompt mit Auszügen aus der Wissensbasis liest genauso: Ohne das Modell lässt er die Auszüge weg und sagt,
  warum. Ebenso Dittos automatische Diagnose eines gescheiterten Builds, nach der niemand gefragt wird. Die Tools,
  die Text speichern (`crawl_docs_site`, `index_framework_docs` und ein Wissens-Pack über `add_pack_from_index`),
  laden die Modelle zuerst: Ihre Frage sagt, ob sie schon auf diesem Rechner liegen oder zuerst einmal
  heruntergeladen werden, mit der Größe und der Situationswarnung `embedding_model_download`.
- **Dokumentation aufnehmen:** [Deep Ingest](#deep-ingest) im Modul-Assistenten und
  [Wissens-Packs](#community-zoo) aus dem Community-Zoo.
- **Die eigenen Dokumente des Frameworks**: **Tools > Index the Framework's Documentation...**,
  `llm-cli knowledge index-docs` oder das Tool `index_framework_docs` nehmen seine Dokumentation (README,
  CONTRIBUTING, den Ordner `docs`) und auf Wunsch seinen Code und seine Konfiguration als Art `docs` auf, damit Ditto
  das Innenleben des Frameworks erklären kann (wie ein Wert der Probe ein Build-Skript erreicht, was eine Einstellung
  bewirkt). Das passiert nie von selbst; vorher wird ein Snapshot angelegt (ohne Platz dafür auf der Platte wird
  nichts aufgenommen, wenig übriger Platz ist eine Warnung), und ein Export als Wissenspaket enthält diese Texte nie.

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
CLI-Befehl dafür gibt es in dieser Version nicht. KI-Clients und Ditto können mit dem Tool `crawl_docs_site` um einen
Crawl bitten ([unten](#deep-ingest-durch-einen-ki-client)); du bestätigst jeden einzeln.

Text- und Data-Mining öffentlich zugänglicher Inhalte ist erlaubt, solange der Rechteinhaber es sich nicht
maschinenlesbar vorbehalten hat (EU-Richtlinie 2019/790, Art. 4; § 44b UrhG). Deshalb prüft der Crawler diese
Signale selbst, statt dich etwas bestätigen zu lassen, und keine dieser Prüfungen lässt sich abschalten. Die frühere
Option *Respect robots.txt* und die Einstellung `crawler_respect_robots` gibt es nicht mehr.

### Der Hinweis vor dem ersten Crawl

- Vor dem ersten Deep Ingest jedes Benutzers erklärt ein Hinweis in der Sprache der Oberfläche, was gelesen wird (nur
  öffentlich erreichbare Seiten ohne Anmeldung, ausgehend von den Adressen, die du eingibst, innerhalb der Seiten-
  und Tiefengrenzen), welche Opt-outs beachtet werden, dass das Wissen auf diesem Rechner bleibt (wenn Ditto
  antwortet, gehen passende Auszüge an den gewählten KI-Anbieter; mit einem lokalen Anbieter verlässt nichts den
  Rechner) und dass Teilen in einem Target oder einem Community-Pack Quelle und Lizenz jeder Seite behält, den
  vollen Text einer Seite nur enthält, wo ihre Lizenz die Weitergabe erlaubt, und deine ausdrückliche Freigabe
  braucht.
- Er ist eine Information, keine rechtliche Bestätigung: eine Schaltfläche, **Verstanden** (englische Oberfläche:
  *Understood*, auch Esc), nichts zum Ankreuzen.
- Er erscheint einmal pro Benutzer und erneut, wenn sich sein Text in einer Sprache ändert. Die Zustandsdatei
  `crawl_notice.json` im persönlichen Ordner (`~/.llm-framework`) speichert einen Hash seiner Texte, wann er gelesen
  wurde und wo (`via`: `gui`, oder `cli` für `llm-cli tools crawl-notice`); das Lesen landet auch im Audit-Log. Lässt
  sich dieser Ordner nicht nutzen, erscheint der Hinweis jedes Mal.
- Nur eine Person bestätigt ihn, in der GUI oder mit `llm-cli tools crawl-notice` in einem Terminal. Solange er nicht
  gelesen ist, wird `crawl_docs_site` mit dem Grund `needs_person` und diesen beiden Orten abgelehnt.
- GUI: Tools > Hinweis zu Deep Ingest... zeigt ihn jederzeit und bittet dich, solange er noch nötig ist, zu
  bestätigen, dass du ihn gelesen hast („Nein“ ist vorgewählt); der Deep Ingest des Modul-Assistenten zeigt ihn vor
  der Prüfung der Wissensdatenbank.

### Was der Crawler beachtet

| Signal | Wo es gelesen wird | Wirkung |
|---|---|---|
| robots.txt (RFC 9309) | `/robots.txt` jeder Site, einmal pro Schema, Host und Port | Gesperrte Pfade werden nicht abgerufen. Es gelten die Gruppen, deren `User-agent` `LLM-Cross-Compiler-Framework-Bot` oder, von vor der Umbenennung, `LLM-Conversion-Framework-Bot` lautet (gleich in welcher Schreibweise), sonst alle `*`-Gruppen (eine Gruppe für `bot` gehört einem anderen Crawler); Muster mit `*` und `$` wirken; die längste Regel gewinnt, bei Gleichstand `Allow`. Eine robots.txt, die nicht erreichbar ist oder mit einem Serverfehler, 401 oder 403 antwortet, gilt als „alles gesperrt“ (bei 401 und 403 strenger als RFC 9309, das dann Crawlen erlaubt: eine verschlossene robots.txt kann einen Vorbehalt enthalten). |
| Header `X-Robots-Tag` | jede Antwort | `noindex`, `none`, `noai` und `noimageai` halten die Seite heraus; bei `nofollow` und `none` werden ihre Links nicht verfolgt. Direktiven für diesen Crawler (`LLM-Cross-Compiler-Framework-Bot: noai` oder sein früherer Name `LLM-Conversion-Framework-Bot`) zählen, solche für andere Crawler nicht. |
| `<meta name="robots">`, `<meta name="llm-cross-compiler-framework-bot">` und das frühere `<meta name="llm-conversion-framework-bot">` | HTML-Seiten | dieselben Direktiven wie beim Header |
| TDM Reservation Protocol (W3C TDMRep 1.0) | `/.well-known/tdmrep.json` pro Site (die erste passende Regel entscheidet), der Header `tdm-reservation`, `<meta name="tdm-reservation">`, `tdm:reservation` in den PDF-Metadaten | Ein Vorbehalt hält die Seite heraus; eine `tdm-policy` wird im Log genannt. |

- Ein Vorbehalt aus irgendeiner dieser Quellen gewinnt. TDMRep erlaubt einer späteren Quelle, einen früheren
  Vorbehalt aufzuheben; der Crawler bleibt auf der vorsichtigen Seite.
- Weiterleitungen werden nicht direkt verfolgt. Ihre Ziele kommen in die Warteschlange und werden wie jede andere
  Adresse geprüft: Sicherheitsprüfung, robots.txt und `tdmrep.json` ihres eigenen Hosts. Ein Link, der auf einen
  anderen Host weiterleitet, wird übersprungen; nur eine Weiterleitung der Start-URL (zum Beispiel von `example.com`
  auf `www.example.com`) verlegt den Crawl auf diesen Host. Höchstens 5 Weiterleitungen pro Adresse.
- Der Crawler nennt sich `LLM-Cross-Compiler-Framework-Bot/<Version> (Open Source;
  +https://github.com/Smilez1985/LLM_Cross-Compiler_Framework)`, mit der echten Version des Frameworks. Betreiber von
  Websites können ihn unter diesem Namen in der robots.txt und in einem Meta-Tag ansprechen. Regeln, die eine Website
  für seinen Namen von vor der Umbenennung des Repositorys geschrieben hat, `LLM-Conversion-Framework-Bot`, gelten
  weiter für ihn, ein früher erklärter Nutzungsvorbehalt bleibt also bestehen.

### Versteckter Text bleibt draußen

Text, den eine Person beim Lesen einer Seite nicht sieht, ist ein bekannter Weg, in einer Webseite Anweisungen für
eine KI zu verstecken (Prompt Injection). Deshalb speichert Deep Ingest nur, was eine Leserin oder ein Leser sieht
(`orchestrator/rag/html_text.py`):

- **Aus jeder gecrawlten Seite fällt heraus,** mit allem, was darin steht, bevor ihr Text und ihre Links gelesen
  werden (einem Link, den niemand sieht, folgt der Crawler auch nicht): Elemente, die ihr eigener Inline-Style
  versteckt (`display:none`, `visibility:hidden`, eine Schriftgröße von 2 px oder weniger, eine Deckkraft von 5 % oder
  weniger, absolute, feste oder relative Positionierung weit außerhalb des Bildschirms oder ein großer negativer
  `text-indent`, ein `clip` oder `clip-path`, der keine sichtbare Fläche übrig lässt, eine Box von 1 px oder weniger
  mit `overflow:hidden`, Text in derselben Farbe wie sein eigener Hintergrund); Elemente mit dem Attribut `hidden`
  (nicht `hidden="until-found"`, das die Suche des Browsers aufdeckt) oder mit `aria-hidden="true"`; `<template>` und
  `<noscript>`; CDATA-Abschnitte, die ein Browser wie Kommentare behandelt. Der inaktive Bereich eines Tab-Widgets
  bleibt (`role="tabpanel"` auf einer Seite mit Tabs): Du öffnest ihn mit einem Klick.
- **Gelesen wie ein Browser liest:** Der Inline-Style zählt so, wie ein Browser ihn anwendet (`!important`,
  CSS-Kommentare und Escapes eingeschlossen), und ebenso zählen durchsichtiger Text (`color: transparent`, eine Farbe
  ohne Deckkraft, außer der Text wird anders gemalt, etwa als Verlaufstext), `transform: scale(0)`, eine Box mit
  `max-height: 0`, ein großer negativer Rand oder eine große Verschiebung, `filter: opacity(0)` und
  `content-visibility: hidden`. Auch Elemente, deren Inhalt ein Browser nie zeigt, fallen heraus: der Ersatztext von
  `<iframe>`, `<canvas>`, `<video>` und `<audio>`, `<noembed>`, `<noframes>`, `<datalist>`, `<rp>`, ein `<dialog>`,
  das nicht offen ist, und ein zweiter `<title>` in der Seite (gezählt als `display:none`). Ein versteckendes Element,
  das als selbstschließender Tag geschrieben ist (`<div hidden/>`), enthält wie im Browser, was danach folgt, und von
  einem doppelt geschriebenen Attribut zählt das erste.
- **Danach gereinigt wie jeder Text von außen,** bevor er gespeichert wird, damit der gespeicherte Auszug das ist, was
  eine KI später zu sehen bekommt (nicht erst im Moment, in dem sie ihn sieht): Unicode normalisiert (NFKC),
  unsichtbare Zeichen entfernt (Zeichen ohne Breite, Unicode-Tag-Zeichen, Bidi-Steuerzeichen und ähnliche) und
  HTML-Kommentare entfernt, dieselbe Reinigung, die jeder Auszug bekommt, bevor er ein Modell erreicht
  (`orchestrator/utils/untrusted.py`). Der Titel der Seite wird ebenfalls gereinigt. Der Text eines PDFs bekommt
  dieselbe Reinigung. Zu diesem Zeitpunkt ist der Text das, was seine Leserin oder sein Leser sieht; ein `<!--`, das
  nichts schließt, ist also sichtbarer Text: Es verliert nur diese vier Zeichen, nie den Rest der Seite.
- **Wissenspakete** folgen derselben Regel, wenn sie HTML enthalten: Eine Seite, die ein ganzes HTML-Dokument ist,
  wird wie eine gecrawlte Seite gelesen, und HTML im Markdown eines Pakets verliert seine versteckten Elemente und
  alles, wovon ein Browser nichts zeigt (Kommentare, CDATA-Abschnitte, Verarbeitungsanweisungen); HTML in Code-Spans
  und Code-Blöcken wird gezeigt, nicht dargestellt, und bleibt. Das Ergebnis wird danach als Markdown (CommonMark)
  dargestellt; versteckt diese Darstellung noch Text (wo Code-Spans, Code-Blöcke oder HTML-Blöcke anders gelesen
  werden), wird stattdessen der sichtbare Text dieser Darstellung gespeichert. Danach dieselbe Reinigung. Das gilt für
  jede Seite, die aus einem Paket stammt, gleich wie sie gespeichert wird.
- **Du siehst, wie viel herausgefallen ist:** **Tools > Wissensquellen...** und `llm-cli knowledge sources` haben die
  Spalte „Ausgelassener versteckter Text“ (CLI: „Hidden text left out“), Art für Art (zum Beispiel „display:none 2,
  unsichtbare Zeichen 14“; Elemente zählen je einmal, unsichtbare Zeichen einzeln); „nichts“ (CLI: „none“), wenn
  nichts gefunden wurde, „-“ für eine Quelle, die ohne diese Prüfung gespeichert wurde (die eigenen Dokumente des
  Frameworks, Seiten von vor dieser Version). Diese Zahlen schreibt nur das Framework selbst (ein Paket kann sie nicht
  setzen); sie gehen nie an eine KI und nie in ein exportiertes Paket, und der entfernte Text wird nirgends
  aufbewahrt.
- **Die eigenen Dokumente des Frameworks** werden so gespeichert, wie sie geschrieben sind: Sie sind kein Text von
  außen.
- **Grenzen:** Es zählt nur, was ein Element selbst sagt. Regeln in Stylesheets und Klassen (wie `.sr-only`) sieht das
  Framework nicht, weil es keine CSS-Engine hat; das Entfernen ist also unvollständig. Ein Element, das eine
  Schriftgröße, Sichtbarkeit oder Farbe versteckt, fällt mit allem heraus, was darin steht, auch mit einem Kind, das
  eine eigene sichtbare Größe oder Farbe setzt. Die Seite wird so gelesen, wie sie ausgeliefert wird, ohne ihre
  Skripte: Text, den ein Skript erst später zeigt (ein Inline-`opacity: 0`, das eingeblendet wird), fällt heraus. Das
  Markdown eines Pakets behält seine HTML-Tags, wie sie geschrieben sind, Attributwerte (`title`, `alt`)
  eingeschlossen. Eine Wissensbasis behält ihre älteren Auszüge, bis eine Seite erneut eingelesen wird (zum Beispiel
  durch ein neues Deep Ingest derselben Adresse) oder ein Paket erneut importiert wird.
- **In der CI geprüft:** Testseiten, die je eine versteckte Nutzlast neben einem sichtbaren Text enthalten
  (`evals/retrieval/carriers/`), laufen in den Tests durch den Crawler und die Wissensbasis, und die nächtliche
  Retrieval-Evaluation führt sie als eigene Gruppe aus (`llm-cli ditto eval --kind retrieval --cases
  evals/retrieval/carriers.yml`): Eine Nutzlast, die in die gespeicherten Auszüge gelangt, oder ein sichtbarer Text,
  der dort fehlt, lässt den Lauf scheitern.

### Lizenzen und Urheberrecht

Text- und Data-Mining erlaubt der Wissensbasis, den Text zu behalten, den der Crawl lesen durfte. Diesen Text
weiterzugeben ist etwas anderes. Deshalb hält der Crawler je Seite fest, was sie über ihre Lizenz und ihr Urheberrecht
sagt, und ein Export entscheidet je Seite danach (`orchestrator/rag/licences.py`; achte Runde, Q-1 und Q-1').

- **Je Seite festgehalten,** aus der ganzen Seite, bevor ihre Fußzeile entfernt wird: die Quelladresse; ein
  Lizenz-Link (`rel="license"` an einem `<link>` oder `<a>`, oder im HTTP-Header `Link`); Lizenz-Metadaten
  (`<meta name="license">`, `dc.rights`, `dcterms.license`, schema.org `license` in JSON-LD; in einem PDF
  `cc:license` oder `xmpRights:WebStatement` in seinen XMP-Metadaten); ein Link auf eine Creative-Commons-Lizenz in
  der Fußzeile der Seite oder in einem Satz, der sagt, dass die Seite unter ihr lizenziert ist; und ein
  ausdrücklicher Urheberrechtshinweis („©“, „(c)“ mit Jahr, „Copyright“ mit Jahr, „All rights reserved“). Zeigen sich
  mehrere Lizenzen, zählt die vorsichtige. Eine Lizenz, die das Framework nicht erkennt, wird als `other` mit ihrem
  Wortlaut festgehalten.
- **Nur was die eigene Lizenz der Seite nennt, zählt.** Eine Lizenz wird durch ihre Adresse, ihre SPDX-ID, ihren
  eigenen Titel mit Version oder ihren üblichen Erlaubnistext genannt. Eine nebenbei erwähnte Lizenz nennt nichts: ein
  Lizenztext, der andere Lizenzen nennt, „not in the public domain“, eine Liste von Fremdkomponenten, ein Link im
  Text eines PDFs. Ein Bildnachweis mit Creative-Commons-Link ist nicht die Lizenz der Seite, und ein bloßer Link
  wiegt nie ein ausdrückliches „All rights reserved“ auf. Eine andere Version einer Lizenz (MPL 1.1, Apache 1.1) gilt
  nicht als die, die die Weitergabe erlaubt.
- **Code von GitHub:** Nur die Lizenzdatei des Repositorys entscheidet (`LICENSE`, `LICENCE` oder `COPYING` oben im
  Repository), und nur wenn derselbe Crawl sie gelesen hat; die Fußzeile von GitHub selbst wird nicht gelesen. Ohne
  sie gilt der Code als ohne Lizenz, also urheberrechtlich voll geschützt. Die Lizenzdatei gilt für den Code (die
  Startseite des Repositorys, seine Dateien und Ordner), nicht für seine Issues, Pull Requests, Diskussionen, sein
  Wiki oder seine Releases: Das sind Worte anderer Leute unter anderen Bedingungen, und sie kommen als Adresse mit
  kurzen Fakten in ein Paket.
- **Was ein Paket enthält:** Ein Wissenspaket und der Wissens-Snapshot, den der Modul-Assistent einem neuen Target
  beilegt, enthalten den vollen Text einer Seite nur, wenn ihre Lizenz die Weitergabe erlaubt: CC0, Gemeinfreiheit,
  CC BY, CC BY-SA, MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense, MPL-2.0. Jeder Auszug behält
  dann die Namensnennung, die die Lizenz verlangt (Titel, Adresse, Urheberrechtshinweis, Lizenz mit ihrer Adresse und
  die Änderungen: in Auszüge geteilt, persönliche Daten entfernt). Jede andere Seite kommt als ihre Adresse mit kurzen
  Fakten hinein (Titel, Abschnittsnamen, Fachbegriffe wie Optionen, Bezeichner und Versionen), mit der Lizenz und dem
  Hinweis, die sie zeigt, und dem Grund: ein Urheberrechtshinweis ohne eine Lizenz, die es erlaubt, Code ohne Lizenz,
  eine Lizenz, die Änderungen oder kommerzielle Nutzung verbietet (NC, ND), eine Copyleft-Lizenz (GPL, LGPL, AGPL,
  GFDL: Sie verlangen den ganzen Lizenztext und die Quelle bei jeder Kopie), eine nicht erkannte Lizenz oder gar
  keine. Wer das Paket bekommt, crawlt diese Seiten unter eigenen Prüfungen.
- **Lizenztexte und NOTICE:** Manche Lizenzen verlangen ihren Text oder Erlaubnishinweis bei jeder Kopie: MIT,
  BSD-2-Clause, BSD-3-Clause, ISC, Apache-2.0, MPL-2.0 sowie CC BY und CC BY-SA 1.0 bis 4.0. Enthält ein Paket Seiten
  unter einer davon mit vollem Text, enthält es auch `LICENSES/<SPDX-ID>.txt` mit dem Text der Lizenz und eine Datei
  `NOTICE`, neben seiner Wissensdatei (in einem Target in `knowledge/`). Die Texte stammen unverändert aus der SPDX
  License List 3.29.0 (ihre Daten stehen unter CC0). Sie liegen in `orchestrator/data/licenses/`, jeder wird über
  seinen sha256 geprüft, und die Datenschutzbereinigung ändert sie nie. `NOTICE` führt jede Seite mit ihrer
  Quelladresse, ihrer Copyright-Zeile wie gefunden und ihrer SPDX-ID auf, nach Lizenz gruppiert. Bei MIT, BSD und ISC
  steht jede Copyright-Zeile bei dem Lizenztext, zu dem sie gehört; die Lizenzdatei eines Repositorys und Code von
  GitHub ohne eigenen Hinweis führen jede Copyright-Zeile dieser Datei auf. Bei Apache-2.0 folgen die Hinweise aus der
  `NOTICE`-Datei des Werks, wenn derselbe Crawl sie gelesen hat (die `NOTICE` oben im Repository oder eine Seite
  namens `NOTICE` auf derselben Website); sonst sagt `NOTICE`, dass keine gelesen wurde. CC0, Gemeinfreiheit, 0BSD und
  die Unlicense verlangen keinen Text. Eine Lizenz, die ihren Text verlangt, deren Text das Framework aber nicht hat
  (eine fehlende oder geänderte Datei), lässt keine Seite mit vollem Text hinein: Eine solche Seite kommt als ihre
  Adresse mit kurzen Fakten hinein, und der Grund sagt warum.
- **Beim Hereinkommen geprüft:** Eine Installation, `llm-cli zoo verify` und die Prüfung des Community-Repositorys
  lehnen ein Paket ab, dessen Lizenztexte nicht Byte für Byte die kanonischen sind (in einem Target-Paket auch ohne
  Wissens-Snapshot), das eine Lizenz nennt, für die das Framework keinen Text hat, oder dem ein Text oder die `NOTICE`
  fehlt, die seine Seiten brauchen. Ein installiertes Paket behält `LICENSES/` und `NOTICE` in seinem Ordner, und jede
  Seite, die es der Wissensbasis hinzufügt, nennt ihren Lizenztext (`licence_text_file`).
- **Du siehst es je Seite:** `llm-cli knowledge sources` listet jede Quelle mit ihrer Lizenz, ihrem
  Urheberrechtshinweis und dem, was ein Paket enthielte, und warum (GUI: **Tools > Wissensquellen...**, mit einem
  Filter nach Art); `llm-cli zoo export knowledge` führt jede Seite
  noch einmal auf, mit dem, was das Paket enthält, und warum, und nennt jeden Lizenztext im Paket und warum. Ein
  Target-Export macht dasselbe für den Wissens-Snapshot des Targets, auch für einen Snapshot von vor dieser Regel (der
  Target-Ordner selbst bleibt, wie er ist); Lizenzdateien, die ohne Snapshot in `knowledge/` liegen, bleiben aus dem
  Paket draußen, und ein Hinweis sagt das.
- **Für KI-Clients:** `list_knowledge_sources` nennt für jede Quelle die Lizenz-ID, den Zustand (`open`,
  `restricted`, `copyright`, `code_without_licence`, `unknown` oder `no_licence_text`, wenn das Framework keinen Text
  einer Lizenz hat, die ihn verlangt) und was ein Paket enthielte; `export_pack` nennt jeden Lizenztext mit seiner
  Datei, der Zahl der Seiten und dem Grund. Der Urheberrechtshinweis selbst ist Text der Website und erreicht nie eine
  KI.

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

### Deep Ingest durch einen KI-Client

Mit `crawl_docs_site` kann ein MCP-Client oder Ditto Dokumentation in die Wissensbasis aufnehmen (Entscheidung vom
11.10.2026): derselbe Crawler mit denselben Opt-out-Prüfungen wie in der GUI, als Job.

- **Immer gefragt, mit jeder Startadresse.** Voreinstellung und Obergrenze sind "nachfragen" (ein "erlauben" pro Tool
  wird gedeckelt), und es bietet nie „ja, und nicht mehr fragen“ an. Die erste Zeile der Frage sagt, was passiert (Websites,
  Seiten je Startadresse, Linktiefe, die Pause, dass die Opt-outs beachtet werden und sich nicht abschalten
  lassen), dann eine Zeile je Startadresse (markiert als vom KI-Client angegeben), die behaltenen Sprachen, PDFs, der
  Snapshot und ob das Embedding-Modell schon auf diesem Rechner liegt oder zuerst einmal heruntergeladen wird (dann
  mit seiner Größe und der Warnung `embedding_model_download`).
- **Vor der Frage geprüft:** Der Hinweis muss gelesen sein (sonst `needs_person`); Offline-Modus aus und Wissensbasis
  an (`not_supported`); 1 bis 5 Startadressen, jede eine öffentliche `http(s)`-Adresse, die die SSRF-Prüfung besteht
  (`invalid_value` für alles andere); `max_pages` je Startadresse von 1 bis `crawler_max_pages` (nie über 100),
  `max_depth` von 1 bis `crawler_max_depth` (nie über 3; Voreinstellung 2); ein Wert über der Grenze wird mit dem
  erlaubten Bereich abgelehnt, nie verkleinert.
- **Nichts erreicht die Opt-outs oder die Pause:** Das Tool nimmt keinen robots-Schalter und keine Pause (ein
  unbekanntes Argument wird abgelehnt); der Crawler wartet mindestens `crawler_rate_limit` und nie weniger als eine
  Sekunde. Der Crawler übergeht jeden anderen Schlüssel in einem Options-Dictionary, robots.txt lässt sich also von
  nirgends abschalten; Tests nutzen einen eigenen Schlüsselwort-Schalter, den kein Produktcode übergibt (ein
  Wächtertest prüft das).
- **Grenzen:** ein Crawl gleichzeitig je Benutzer (auch über Framework-Prozesse hinweg, Sperrdatei `crawl.lock` im
  persönlichen Ordner), 5 Crawls am Tag je Client und höchstens 300 gespeicherte Seiten am Tag je Client. Das
  Seitenbudget und der Offline-Modus werden beim Start eines freigegebenen Crawls erneut geprüft, damit später
  beantwortete Fragen zusammen nie über das Budget gehen.
- **Als Job:** Der Aufruf gibt eine Job-ID zurück; `get_job_status` zeigt "Seite n von höchstens m" (keine Adresse);
  `cancel_job` hält nach der aktuellen Seite an und behält, was gespeichert ist. Vor jeder Startadresse wird ein
  Snapshot angelegt. Das Ergebnis enthält den Ausgang (`success`, `partial`, `failure`), gespeicherte Seiten und
  Abschnitte, die übersprungenen Adressen mit Grund und ob eine Website sie vorbehalten hat (höchstens 50), die
  fehlgeschlagenen (höchstens 50) und den Namen des Snapshots; nie Seitentext (der Client liest Seiten mit
  `search_knowledge` oder `read_knowledge_source`). Adressen und Gründe im Ergebnis eines Jobs sind externer Text und
  werden wie anderer externer Text markiert und bereinigt.
- Die Seiten sind als `crawled` markiert, wie die eines Deep Ingest in der GUI.

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

**GUI:** Security > Verbindungen zu KI-Apps (MCP)... zeigt, wie du Claude Code, Claude Desktop (mit dem Pfad seiner
Konfigurationsdatei) und VS Code verbindest, jeweils mit einer Schaltfläche zum Kopieren, trägt den Eintrag in die
Konfiguration von Claude Desktop ein, nachdem es Datei und Änderung gezeigt hat (In Claude Desktop eintragen..., die
Sicherung wie bei `mcp config --write`), startet und stoppt den lokalen HTTP-Server als Kindprozess der GUI (Adresse,
Port, Container-Bindung; dieselben Ablehnungen wie `mcp serve --transport http`; Adresse, Zustand und Ausgabe zu
sehen; das Framework fragt vor dem Schließen, solange der Server noch läuft) und verwaltet die benannten Tokens
(Hinzufügen, auch als Kopie der Berechtigungen eines anderen Tokens, Rotieren, Widerrufen samt den dauerhaften
Freigaben des Tokens, Zurück auf nur lesen). Ein Token wird einmal gezeigt, direkt nachdem es angelegt oder rotiert
wurde.

- **Jeder Aufruf läuft durch die Tool-Registry:** Abgleich von Namen und Argumenten, Eingabeschema, deine
  Berechtigungen, Bestätigung, Audit-Log (Akteur `mcp` für lokale stdio-Clients, `mcp:<Name>` für ein benanntes
  Token), genau wie für Ditto ([wie Namen und Werte abgeglichen werden](#dittos-tools)). Tool-Annotationen (nur
  lesend, idempotent) sind nur Hinweise für den Client.
- **Builds (`start_build`, `check_build_request`):** Bevor du gefragt wirst, schlägt das Framework nach, was der Build
  tun würde, und zeigt es in der Frage: den Hugging-Face-Commit, auf den der Standardzweig jetzt zeigt (der Build holt
  dann genau diesen Commit, auch wenn der Zweig sich ändert, während du liest), die Downloadgröße, die Art der
  Gewichte (safetensors, GGUF oder Pickle-Dateien, die im Build-Container Code ausführen können und so benannt
  werden), die Lizenz (Text aus dem Repository, so gekennzeichnet), die Zugangsbeschränkung und ob das im Framework
  gespeicherte Hugging-Face-Token Zugriff hat, den Speicherplatz auf jedem Laufwerk, auf das der Build schreibt, eine
  Schätzung, ob dieser Rechner es konvertieren und das Board es ausführen kann, was eingebunden wird (Labels, nie
  Pfade), Vertrauen und Support-Stufe des Targets und dass zuerst ein Docker-Image mit Netzwerkzugang gebaut werden
  kann. Es gibt keine Größengrenze für Downloads: Ein 72B-Modell wird gebaut, wenn die Laufwerke es fassen. Vor der
  Frage abgelehnt wird ein Build nur, wenn ein Laufwerk den Download, die Konvertierungsreserve (etwa noch einmal der
  Download) und die Ausgabe dreifach (Build-Cache, bereitgestellte Kopie, ZIP) plus eine Reserve von 2 GiB oder 2 %
  des Laufwerks und den Platz, den andere angenommene Builds reserviert und noch nicht geschrieben haben, nicht fasst
  (Grund `insufficient_resources`, mit den Zahlen); wenn bei einem zugangsbeschränkten Modell der Zugriff fehlt
  (`needs_person` ohne gespeichertes Token, `permission_denied`, wenn das gespeicherte Token keinen Zugriff hat); wenn
  die Build-Sandbox aus ist (`build_sandbox`, `needs_person`); und im Offline-Modus. Die Hardware-Schätzung ist nie
  eine Ablehnung: „passt nicht“ wird gezeigt, und du entscheidest. Ist Hugging Face nicht erreichbar, sagt die Frage
  „Standardzweig, Commit unbekannt“ und dass die Downloadgröße unbekannt ist. Ein angenommener Build reserviert
  seinen Speicherplatz in `~/.llm-framework/disk_reservations.json` (gemeinsam für GUI, CLI und jeden MCP-Server),
  bis seine Anfrage endet; verlässt der Build die Warteschlange, wird das Laufwerk erneut geprüft, und ein Build, der
  nicht mehr passt, scheitert mit den Zahlen, bevor etwas heruntergeladen wird. `download_hf_file` reserviert seine
  Datei im selben Verzeichnis, sodass ein Download und ein Build nie mit demselben freien Platz rechnen. Der Build
  stellt das Modell über den Hugging-Face-Abrufer des Frameworks bereit: Datei für Datei, jede aus ihrer
  `.part`-Datei fortsetzbar und gegen Größe und sha256 geprüft, die der Hub nennt, und durch einen Abbruch binnen
  Sekunden gestoppt. Ein Laufwerkswächter stoppt das Bereitstellen und die Konvertierungs-Container, wenn das
  Cache-Laufwerk unter die halbe Reserve fällt (Grund `insufficient_resources`). Ein Dauerauftrag („ja, und nicht mehr
  fragen“) wird angeboten für Builds auf mitgelieferten oder verifizierten Targets mit safetensors- oder
  GGUF-Gewichten, einem nicht zugangsbeschränkten Modell, dessen Basismodell es auch nicht ist, einer Lizenz auf der
  Liste freizügiger Lizenzen (oder einer lokalen Datei), Docker Official Images als Basis (oder einem per Digest
  gepinnten Basis-Image, das du in einem Build dieses Targets schon bestätigt hast; die Frage eines Builds auf einem
  nicht mitgelieferten Target nennt jedes Image jedes Dockerfiles, auch späterer Stufen), bis zur gewählten Größenklasse
  und nur, wenn das Laufwerk die größere Reserve von 20 GiB oder 10 % behält. Diese größere Reserve wird beim Start
  des Builds erneut geprüft, im Reservierungsbuch, das alle Framework-Prozesse teilen, und unter seiner Sperre, so
  dass eine inzwischen von einem anderen Prozess genommene Reservierung mitzählt; reicht sie nicht mehr, startet
  nichts und der nächste Aufruf fragt dich. Ein Target- oder Rezept-Pack, das eine KI unter einem Dauerauftrag
  installiert hat, ist erst abgedeckt, nachdem du einen Build davon im eigenen Fenster des Frameworks bestätigt
  hast (Dittos Dialog oder Abfrage oder eine ausstehende Freigabe; eine Antwort im Formular des Clients zählt
  nicht); das Framework vermerkt diesen Build und deckt die folgenden ab; Hugging-Face-Abfragen von
  `check_build_request` zählen zur Hugging-Face-Rate von 30 pro Stunde. `optimization` (`fast`, `balanced`,
  `size`, `speed`, `aggressive`) wird an den Build weitergegeben. `check_build_request` liefert denselben Plan
  (normalisierte Anfrage, Commit, Größen, Laufwerke, Passung, Einbindungen, Vertrauen, Image-Build, was deine
  Berechtigungen tun würden), ohne etwas zu starten; seine Probleme tragen Grundcodes.
- **Build-Abfragen:** `list_build_requests` (50 pro Seite, Filter nach Status) listet auch Builds, die ein anderer
  Framework-Prozess gestartet hat (GUI, CLI, ein anderer MCP-Server), aus dem gemeinsamen Build-Zustand, markiert
  mit `process: other` und `started_in` und nur lesbar (gestoppt werden sie dort, wo sie gestartet wurden; ein Build,
  dessen Prozess endete, ist `interrupted`); der Hinweis, nur Builds dieses Prozesses seien sichtbar, entfällt. `get_build_status` ergänzt, was die Anfrage baut (`request`: Modell als Repo-ID oder
  Label, Target, Format, Board, Quantisierung, Optimierung, Commit). `read_build_log` liefert höchstens 1000 Zeilen
  (Standard 200), 2000 Zeichen pro Zeile und 48.000 insgesamt, mit `first_line` und `before_line` zum
  Zurückblättern. `list_artifacts` benennt gebaute Modelle mit einer ID (`a_` und 16 Hex-Zeichen) und einem Label
  (`output:<relativer Pfad>`) mit Größe und Hash, 50 pro Seite, gefiltert nach Target und Format;
  `get_artifact_details` liefert Manifest (jeder Text als externer Text gekennzeichnet, der Pfad eines lokalen
  Modells als sein Label), Model Card und Dateien eines Modells. Die Ressourcen
  `lcf://artifacts/{artifact_id}/manifest` und `lcf://artifacts/{artifact_id}/model-card` lesen dasselbe.
- **Status, Einstellungen und ein ehrliches „kann nicht“ (`get_status`, `get_settings`, `update_settings`,
  `report_cannot_do`):** `get_status` zeigt den Zustand des Frameworks in Abschnitten: `framework` (Version, Docker,
  Targets, Offline-Modus, Wissensbasis, laufende Builds, ob die Berechtigungen eingerichtet oder beschädigt sind),
  `host` (Betriebssystem, Architektur, RAM, GPUs und der empfohlene Build und die Modellstufe der lokalen Laufzeit),
  `local_runtime` (der selbst gebaute llama.cpp-Server: Herkunft, Build-ID, Commit, Backend, Prüfung, läuft, Stufe;
  ein per Opt-in geladenes vorgebautes Archiv heißt „nicht selbst gebaut“; nie seine Adresse oder sein Schlüssel),
  `knowledge` (an oder aus, verbunden, Vektoren, Speicherort als „der persönliche Ordner“ oder Schema und Host,
  Embedding-Modell, Snapshots), `ai_access` (je Anbieter Anmeldestatus, Verfahren und woher die Zugangsdaten kommen,
  und ob ein Hugging-Face-Token gespeichert ist; nie ein Schlüsselteil, Konto oder Endpunkt), `permissions` (wie
  bisher) und `audit` (die letzten 30 Einträge dieses Aufrufers und ob die Kette intakt ist). `get_settings` liest
  eine feste Liste von Einstellungen: Adressen nur als Schema und Host, Ordner als Labels (`models:`), nie
  `secret_helper`, `secret_storage` oder die Einstellungen des KI-Anbieters über eine Zusammenfassung hinaus.
  `update_settings` ändert gewöhnliche Einstellungen (Sprache, GUI-Thema, Kalibrierungssprache, Crawler-Sprachen,
  Reranking als Standard, Leerlaufzeit der Modelle, `build_jobs`, Chat-Kontext, Importance Matrix als Standard); du
  wirst mit altem und neuem Wert gefragt, und jede Änderung wird in der `config.yml` gespeichert. Einstellungen, die
  begrenzen, was eine KI oder ein Build darf, lassen sich nur verschärfen (`offline_mode`, `enable_security_scan` und
  `build_sandbox` auf true, `enable_telemetry`, `enable_rag_knowledge` und `crawler_download_pdfs` auf false, Seiten-
  und Tiefengrenzen und Container-Grenzen niedriger, die Pause beim Crawlen länger; ein Speicherlimit von 0 bedeutet
  für Docker gar keins und zählt daher als Lockerung); nur solche Aufrufe dürfen auf „erlauben“ gestellt werden.
  `offline_mode` und `enable_rag_knowledge` laufen zusätzlich durch die Regeln des Dialogs der KI-Einstellungen
  (`AISettingsChange`): Offline-Modus mit einem Cloud-Provider wird mit dem Text des Dialogs abgelehnt, du wählst
  also zuerst einen lokalen Provider.
  Einstellungen, die dich darüber informieren, was KIs tun (gesprochene Ansagen), kann keine KI ändern, in keine
  Richtung. Berechtigungen, Geheimnisse, der Tresor, Einstellungen des KI-Anbieters,
  Adressen, Image-Referenzen, Ordner und die lockernde Richtung werden vor der Frage abgelehnt, und kein Wert wird je
  als „Standard“ gelesen. `report_cannot_do` ist für eine Aufgabe, die die KI wirklich nicht erledigen kann: Sie nennt
  den Grund (`missing_information`, `not_supported`, `no_tool`, `permission_denied`, `declined`, `needs_person`,
  `hardware_unavailable`, `insufficient_resources`, `limit_reached`, `failed`; ein „ungetestet“ gibt es nicht), und
  das Framework prüft ihn an seinem eigenen Zustand (deine Berechtigungen, die Audit-Einträge und Grenzen dieser
  Sitzung, das Laufwerk, die Aktionen, die kein Tool tut) und antwortet `confirmed`, `contradicted`, `premature`
  (noch nichts versucht, mit den Tools, die zu versuchen sind) oder `unchecked`, mit Fakten und einem Satz für dich,
  der nur aus diesen Fakten gebaut ist (ein Tool steht dort nur mit seinem registrierten Namen, nie so, wie die KI
  ihn geschrieben hat). „Die Hardware ist nicht verfügbar“ wird für jedes Tool widersprochen, das
  kein Gerät nutzt: Ungetestete Hardware ist kein Grund abzulehnen. Jede Meldung wird protokolliert
  (`cannot_reported`); `llm-cli tools audit --summary` zählt sie je Aufrufer.
- **Bestätigung:** Wo deine Berechtigung „fragen“ sagt, fragt der Server den Benutzer über den Client
  (MCP-Elicitation). Die Frage zeigt das Tool und jedes Argument vollständig, die sicherheitsrelevanten zuerst;
  Steuerzeichen, unsichtbare Zeichen und lange Stapel kombinierender Zeichen erscheinen als `\u`-Escapes. Eine
  Frage, die länger als 4000 Zeichen würde, wird zu einer wartenden Freigabe (unten) statt gekürzt. Nur eine echte
  Antwort des Benutzers zählt
  als Zustimmung, und ein „Nein“ gilt für diesen Aufruf endgültig, auch wenn die Berechtigungen gelockert werden,
  während die Frage offen ist. Abgelehnte und abgebrochene Aufrufe sowie Aufrufe, die abgewiesen werden, bevor sie
  laufen (ungültige Argumente, eine gescheiterte Bestätigung), landen ebenfalls im Audit-Log.
- **Wartende Freigaben:** Kann ein Client dich nicht fragen (er hat keine Formular-Elicitation erklärt), ist die
  Frage länger, als eine Elicitation sein darf, oder hast du das Tool auf Bestätigung im Framework gestellt
  (`llm-cli tools permissions --confirm-in-framework TOOL` oder „Ask in the framework“ im Dialog Tool Permissions;
  nützlich für Tools, die du jedes Mal im Framework sehen willst), wird der Aufruf nicht abgelehnt. Der Client bekommt
  den Fehler `waiting_for_approval` mit einer Job-ID („poll get_job_status with wait_seconds=50; do not call this
  tool again“), und die Frage wartet auf dich: ein Hinweis in der Statusleiste der GUI (und eine
  Desktop-Benachrichtigung, wo das System eine anbietet), der **Tools > Pending Approvals...** öffnet, und
  `llm-cli tools approvals` in einem Terminal. Das Fenster zeigt zuerst die Aufruferklasse („all local stdio clients“
  oder den Namen des Tokens), dann den eigenen Namen des Clients, gekennzeichnet als selbst angegeben, die
  vollständige Frage (bis zu 16.000 Zeichen; eine längere wird überall abgelehnt) und die verbleibende Zeit, mit
  **Approve once** und **Decline**. Bei der Freigabe baut das Framework die Frage neu: Lautet sie anders (ein Fakt
  dahinter hat sich geändert), läuft nichts und du wirst erneut gefragt; sonst führt das Framework den Aufruf selbst
  aus, unter dem ursprünglichen Aufrufer (`confirm_channel` `approval:<id>` im Audit-Log), und der Client liest das
  Ergebnis mit `get_job_status`. Derselbe Aufruf, wiederholt während er wartet, bekommt denselben Job, eine Freigabe
  läuft einmal und nur für den Aufrufer, das Tool und die Argumente, für die gefragt wurde, sie verfällt nach 15
  Minuten (festgehalten als `unanswered`) und endet mit der Sitzung des Clients, wenn der Client seinen Job abbricht
  oder wenn das Framework beendet wird. Ein Client hat höchstens 5 offene Freigaben. Das Fenster setzt nie einen
  anderen Aufruf an die Stelle dessen, den du gerade liest: Wird er zurückgezogen, läuft er ab oder wird er anderswo
  entschieden, ist nichts ausgewählt; wird er mit neuen Fakten erneut gefragt, ist seine neue Frage gekennzeichnet und
  die Freigabe wartet einen Moment, damit ein Klick, der dem alten Text galt, nicht den neuen freigibt. Deine
  Entscheidung wird festgehalten (`approval_decided`, Akteur `gui` oder `cli`), bevor sie gespeichert wird; scheitert
  das, wird nichts entschieden. Die Freigaben liegen in `~/.llm-framework/tool_approvals.json`, signiert wie die
  Berechtigungen, damit eine Bearbeitung von Hand nichts entscheidet; kein Tool kann eine anlegen, auflisten,
  freigeben oder ablehnen, und nur der Client, der gefragt hat, sieht das Ergebnis. Ditto im Framework fragt immer in
  seinem eigenen Dialog oder seiner Rückfrage und braucht nie eine.
- **Dauerhafte Freigaben („ja, und nicht mehr fragen“):** ein begrenztes „erlauben“ für einen Aufrufer, das nur du im
  eigenen Fenster des Frameworks anlegst: **Approve and do not ask again...** im Freigabefenster,
  `llm-cli tools approvals approve ID --always` oder Dittos Dialog und Rückfrage. Der eigene Dialog eines MCP-Clients
  kann nur darum bitten: Sein Formular hat dann „ask the framework to stop asking for calls like this one“, was
  diesen einen Aufruf ausführt und eine Anfrage ins Freigabefenster legt, die du bestätigst (eine offene Anfrage je
  Aufrufer und Tool, 15 Minuten oder bis seine Sitzung endet; ein abgelehnter Rahmen wird 24 Stunden lang nicht erneut
  angefragt). Bevor du bestätigst, zeigt das Fenster eine Warnung, die nennt, wer dann ohne Nachfrage handeln darf
  (bei den lokalen stdio-Clients: alle, mit dem Befehl, der einem Client ein eigenes Token gibt), was die dauerhafte
  Freigabe in einfachen Worten abdeckt, wie weit („this one only“ oder „any“, wo angeboten) und wie lange („until I
  revoke it“, 30 Tage oder 24 Stunden; für alle lokalen stdio-Clients zusammen nur der engste Rahmen und höchstens 30
  Tage). Der Rahmen kommt aus Fakten, die das Framework für den Aufruf nachgeschlagen hat, nie aus dem, was die KI
  geschrieben hat; ein Aufruf außerhalb fragt wie bisher, und eine dauerhafte Freigabe hebt nie ein „verweigern“ auf,
  deckt nie einen anderen Aufrufer ab und gilt nie für ein Tool, das du auf Bestätigung im Framework gestellt hast.
  Pakete aus dem Zoo können eine bekommen für Wissens- und Rezept-Pakete mit Vertrauensstufe `verified` oder
  `community` und für Target-Pakete mit `verified`, jeweils gebunden an genau die Paketart, die Vertrauensstufe und
  den Signaturschlüssel des Index; Builds und Einzeldatei-Downloads nur für safetensors oder GGUF (nie Pickle-Gewichte),
  Modelle ohne Zugangsbeschränkung mit einer freizügigen Lizenz, bis zu einer Größenklasse; nie angeboten wird sie für
  Deployment, Einstellungen, Crawlen, das Anlegen von Modulen, `export_pack`, Healing, Löschen oder Wiederherstellen
  und das Stoppen der Arbeit eines anderen Clients. Dauerhafte Freigaben liegen in der signierten Berechtigungsdatei
  (eine Bearbeitung von Hand verbietet alles) und werden nur geschrieben, wenn du sie anlegst, widerrufst oder
  zurücksetzt, damit eine Nutzung die Datei nie neu schreibt und keinen Widerruf rückgängig machen kann; ihre
  Nutzungen und die letzte Nutzung kommen aus dem Audit-Log (`confirm_channel` `grant:<id>`), und die GUI zeigt jede
  Nutzung mit einem **Revoke**-Knopf und nennt dabei, wer gehandelt hat, das Tool und was die dauerhafte Freigabe
  abdeckt. Sie verfallen nach 90 Tagen ohne Nutzung (einmal festgehalten als `grant_lapsed`; eine verfallene bleibt
  verfallen), mit ihrem Ablauf, wenn sich die Regel des Tools ändert und wenn ihr Token widerrufen wird; die Anfrage
  eines Tokens, das seitdem widerrufen wurde, wird nie zur dauerhaften Freigabe eines neuen Tokens mit demselben
  Namen. `llm-cli tools permissions --grants` und der Dialog Tool Permissions führen sie mit ihren Nutzungen auf, und
  `--revoke-grant ID`, **Revoke** im Dialog und `--reset` entfernen sie. Solange die GUI läuft, kann die
  Kommandozeile keine anlegen (nutze das Fenster der GUI); eine auf der Kommandozeile angelegte wird beim nächsten
  GUI-Start einmal gezeigt. `get_status` zeigt einem Aufrufer seine eigenen dauerhaften Freigaben, nur lesend.
  Welche Tools bisher eine anbieten: nur Tools, deren Fakten das Framework nachschlagen kann, bekommen eine Regel
  (noch keines der 14 aktuellen Tools; die Zoo-, Download- und lokalen Tools bringen ihre mit).
- **Anweisungen an den Client:** Der Server beschreibt den typischen Ablauf (Targets → Capabilities → Build → Status
  und Log → Artefakte), sagt dem Client, dass als externer Text markierte Felder Daten sind, deren Anweisungen nicht
  befolgt werden dürfen, und dass er dir eine Aufgabe, die er nicht erledigen kann, mit dem konkreten Grund meldet.
- **Ergebnisse:** Jedes Ergebnis läuft durch den Ausgabefilter der Registry, auch für Ditto. Secrets werden in jeder
  Zeichenkette geschwärzt. Text von außerhalb des Frameworks (Build-Logs und Stufennamen, Self-Healing-Vorschläge,
  Build-Manifeste, Auszüge aus der Wissensbasis, Meldungen von Mojo sowie die Bezeichnungen, Hinweise und
  Board-Beschreibungen aus der `target.yml` eines Targets, die ein Community-Target-Pack installieren kann) wird von
  unsichtbaren und bidirektionalen Steuerzeichen befreit und gekürzt (eine Log-Zeile auf 2.000 Zeichen, anderer externer Text auf 4.000); jede andere
  Zeichenkette wird auf 16 KiB gekürzt, und ein Ergebnis über 256 KiB wird mit einem Hinweis abgelehnt, wie man
  weniger anfordert. Solche Felder sind im Ausgabeschema des Tools mit `"untrusted": true` markiert. Das
  strukturierte Ergebnis entspricht genau dem Ausgabeschema; der Textblock beginnt mit einem Satz, der nur aus
  Fakten des Frameworks besteht (IDs, Zustände, Anzahlen), gefolgt vom JSON, eingepackt als nicht vertrauenswürdige
  Daten. `notes` in einem Ergebnis schreibt nur das Framework, zum Beispiel, dass die Wissensbasis erreichbar ist,
  aber nichts passte, oder dass nur die Builds dieses Prozesses aufgeführt sind. Der Schritt eines
  Self-Healing-Vorschlags kommt als `manual_step_text`: Text zum Lesen für dich, nie ein Befehl, den der Client
  ausführen soll. Der Fehler eines gescheiterten Aufrufs kann eine Serverantwort, eine Log-Zeile oder einen Namen
  aus einem Pack zitieren: Er wird vor dem Schwärzen der Secrets genauso bereinigt, und der Client bekommt diesen
  Teil als nicht vertrauenswürdige Daten eingepackt, während die eigenen Ablehnungen des Frameworks und der nächste
  Schritt unverpackt bleiben (Dittos Modell bekommt solche Fehler ebenfalls in einem Block für nicht
  vertrauenswürdige Daten).
- **Ressourcen:** Neben den Tools bietet der Server Ressourcen, die ein Client anhängen oder lesen kann:
  `lcf://targets`, `lcf://targets/<Target>/capabilities`, `lcf://builds/<Request-ID>/status`,
  `lcf://builds/<Request-ID>/log` (die letzten 200 Zeilen), `lcf://permissions`,
  `lcf://knowledge/sources/<Quellen-ID>` (die ersten Auszüge einer Wissensquelle, wie `read_knowledge_source`),
  `lcf://packs/index` (der zwischengespeicherte Community-Index und die installierten Packs, wie `list_packs`),
  `lcf://knowledge/doc-sources` und `lcf://knowledge/doc-sources/<Probe-ID>` (die Dokumentations-Links des
  Quellenkatalogs, alle oder die für Hersteller und SDK einer Probe; nur lesend, ein Crawl bleibt `crawl_docs_site`
  mit seiner Frage) sowie die Dokumente der Installation
  `lcf://docs/<Name>` (`CLI_Usage`, `Features`, `Module_Developer_Guide`, `DESIGN`, `ROADMAP`, `BLUEPRINT` (das
  Verzeichnis des Bauplans) und ihre deutschen
  `_DE`-Zwillinge; aufgeführt werden nur die vorhandenen). Eine Ressource liefert, was ihr Lese-Tool liefert, und wird
  über die Tool-Registry gelesen: Deine Berechtigung für dieses Tool entscheidet (die Ressource eines verbotenen Tools
  wird weder aufgeführt noch gelesen, und eine, deren Tool auf „fragen“ steht, wird mit dem Tool abgelehnt, das
  stattdessen aufzurufen ist, weil das Lesen einer Ressource dich nicht fragen kann), derselbe Ausgabefilter bereinigt
  sie (externer Text als nicht vertrauenswürdige Daten eingepackt, daher kommen diese Ressourcen als Text), und jedes
  Lesen landet als `read_resource:<URI>` im Audit-Log. Eine unbekannte ID, ein Dokument außerhalb der Liste oder ein
  Parameter mit `..`, einem absoluten Pfad oder einem NUL-Byte ist ein Fehler wegen ungültiger Parameter (-32602),
  ebenfalls im Audit-Log, auch wenn der Server ihn ablehnt, bevor eine Ressource läuft (eine unbekannte URI oder ein
  anderes Schema). Ein Dokument, das sich nicht lesen lässt, wird ohne seinen Pfad abgelehnt.
- **Änderungsereignisse:** Ein Client mit Protokoll 2026-07-28 kann auf Änderungen hören (`subscriptions/listen`):
  Die Listen der Tools, Prompts und Ressourcen ändern sich, wenn sich deine Berechtigungen oder die eines Tokens
  ändern, und die Status- und Log-Ressourcen eines Builds melden jede Änderung, nur an den Aufrufer, der den Build
  gestartet hat. Der Server veröffentlicht sie über seinen eigenen Ereignisbus; er prüft jede Sekunde auf Änderungen.
  Auf den älteren Handshake-Revisionen kündigt er keine Änderungsmeldungen an, daher sehen diese Clients eine
  geänderte Berechtigung bei ihrer nächsten Sitzung.
- **Prompts:** Dittos Aufgaben für einen Client, der Dittos Arbeit übernimmt, mit demselben Aufgabentext und
  demselben Wissenskontext, den Ditto bekommt (die meisten Clients zeigen sie als Slash-Befehle):
  `ask_about_framework` (eine Frage; Auszüge aus der Wissensbasis und die Dokumentationsressourcen),
  `convert_model_for_board` (Modell, Target, Board; die Capabilities des Targets und die Schritte von der Prüfung bis
  zum gebauten Modell), `recommend_build_settings` (Modell, Target, Board, `use_knowledge`; die Formate und
  Quantisierungstypen, die das Target für das Board anbietet) und `diagnose_build_failure` (ein Build-Request; sein
  Zustand, das Ende seines Logs und drei Auszüge aus der Wissensbasis). `add_board` und `deploy_to_board` sind fertig
  und werden angeboten, sobald es die Tools gibt, zu denen sie führen (ein Target-Modul anlegen, Pakete bauen und
  übertragen). Ein Prompt gewährt nichts: Jeder Schritt läuft weiter über die Tools und deine Berechtigungen. Seine
  erste Nachricht ist der eigene Text des Frameworks, bei jedem Aufruf derselbe; die zweite enthält den Kontext, in dem
  jeder Wert von außerhalb des Frameworks (auch deine Argumente) als nicht vertrauenswürdige Daten eingepackt ist,
  Secrets geschwärzt sind und der ganze Prompt auf 24.000 Zeichen begrenzt ist. Jeder Prompt trägt die Regel für das
  ehrliche „kann ich nicht“ (siehe [Ditto](#ehrliches-kann-ich-nicht-und-ungetestete-hardware)). Ein Prompt wird wie
  ein Lese-Tool gelesen: Deine Leseberechtigung entscheidet, ein unbekanntes Argument wird mit den gültigen Namen
  abgelehnt (nie verworfen), und jeder Abruf landet als `get_prompt:<Name>` im Audit-Log. Ein Prompt zeigt nichts,
  was ein Lese-Tool nicht darf: Einer, dessen Kontext enthält, was ein Lese-Tool liefert (Zustand und Log eines
  Builds bei `diagnose_build_failure`, die Capabilities eines Targets bei `convert_model_for_board` und
  `recommend_build_settings`), wird weder aufgeführt noch geliefert, solange du dieses Tool verbietest, und mit dem
  aufzurufenden Tool abgelehnt, solange es auf „fragen“ steht; Auszüge aus der Wissensbasis kommen nur, solange
  `search_knowledge` erlaubt ist. Die eigenen Texte des Targets (Format-Bezeichnungen und Hinweise aus
  `target.yml`) sind ebenfalls externer Text.
- **Vervollständigung:** Clients, die Argumente vervollständigen (VS Code), bekommen Target-IDs, die Boards eines
  Targets, Dokumentnamen und die eigenen Build-Requests des Aufrufers, nur für Prompts und Ressourcen, die der
  Aufrufer nutzen darf.
- **Lokale Dateien für KI-Clients:** MCP-Clients und Ditto nennen nie einen Pfad. Ein lokales Modell oder ein
  Kalibrierungsdatensatz wird als `<Label>:<relativer Pfad>` in einem Ordner genannt, den die KI nutzen darf, zum
  Beispiel `models:qwen2.5-0.5b-instruct` oder `datasets:calib/de.txt`, oder über die Datei-ID (`lf_` und 16
  Hex-Zeichen), die das Framework dafür festgehalten hat. Die Ordner sind der Modellordner (`models`, Einstellung
  `models_dir`), der Datensatzordner (`datasets`, Einstellung `datasets_dir`, standardmäßig neben dem Modellordner)
  und die Ordner, die du mit [`llm-cli tools folders add`](CLI_Usage_DE.md#tools-folders) oder in der GUI (Tools >
  Folders the AI May Use... > Add folder..., das auch fragt, was im Ordner liegt: Modell-Dateien, Datensatz-Dateien
  oder beides) hinzufügst. Für den Modell- und den Datensatzordner gelten dieselben Grenzen wie für
  einen hinzugefügten Ordner: Zeigt eine Einstellung auf `/`, dein Home oder einen Ordner, der den persönlichen
  Ordner enthält, ist ihr Label nicht nutzbar. Die Datei muss in ihrem Ordner liegen (Links werden verfolgt und
  geprüft) und außerhalb jedes gesperrten Ordners: des persönlichen Ordners des Frameworks (außer den Datenordnern,
  die eine schreibgeschützte Installation dort hält), `~/.ssh`, `~/.gnupg` (und `GNUPGHOME`), `~/.config` (und
  `XDG_CONFIG_HOME`), Schlüsselbunde, `~/.password-store`, `~/.netrc`, `~/.git-credentials`, `~/.docker`, `~/.aws`,
  `~/.kube`, der eigenen Anmeldungen und Einstellungen der KI-Clients (`~/.claude`, `~/.claude.json`, `~/.codex`,
  `~/.gemini`, `~/.cursor`), des Hugging-Face-Ordners mit seinen Token-Dateien (sein Unterordner `hub` lässt sich
  als Ordner hinzufügen), Browserprofile, `/etc`, `/proc`, `/sys`, `/dev`, `/root` (außer es ist dein eigenes Home)
  und unter Windows des Systemordners (`%SystemRoot%`), der Schlüssel des Computers sowie der Ordner und Tresore für
  Anmeldedaten. Innen und außen werden über die Identität der Datei entschieden, nicht
  über Namensvergleiche, deshalb kann ein Dateisystem ohne Unterscheidung von Groß- und Kleinschreibung `~/.SSH`
  nicht verstecken. Eine Datei mit mehr als einem Hardlink wird abgelehnt. Die Datei wird festgehalten (Identität,
  Größe, Änderungszeit) und unmittelbar vor jedem Start eines Build-Containers erneut geprüft; eine inzwischen
  ersetzte Datei stoppt den Build. Ein Ordner (ein Hugging-Face-Modellordner) wird nur genutzt, wenn er echt
  unterhalb seines Label-Ordners liegt und weder einen gesperrten Ordner noch eine Datei mit Hardlink enthält und
  nur Dateien, Ordner und Links (keinen Socket, kein Gerät, keine Pipe); ein Snapshot aus dem Hugging-Face-Cache wird
  Datei für Datei eingebunden. Dasselbe gilt für den Ordner einer RKNN-Beispielliste, deshalb wird eine Liste direkt
  in einem Label-Ordner abgelehnt. Eine Hugging-Face-Repo-ID von einem KI-Client wird immer heruntergeladen, nie als
  Ordner auf diesem Rechner nachgeschlagen. Ablehnungen nennen das Label, nie den echten Pfad. GUI- und CLI-Builds,
  die du selbst startest, nehmen weiterhin jeden Pfad. Was ein KI-Client zurückliest, nennt ein solches Modell
  ebenfalls beim Label, nie bei den Ordnern auf diesem Rechner: der Stufentext des Builds ("Building
  models:qwen/model.gguf for Rockchip"), der Kontext der Selbstheilung, `model_source` im Build-Manifest und
  `list_artifacts` (ein Host-Pfad, den ein älteres Manifest festhielt, erscheint als `<label>:<relativer Pfad>`, wenn
  er in einem Ordner liegt, den die KI nutzen darf, sonst nur als Dateiname).
- **Hugging Face und lokale Dateien für KI-Clients:** `search_hf_models` listet Modelle, die meistgeladenen zuerst;
  sein Filter `gated` ist standardmäßig `free` (nur Modelle, die ohne Annahme von Bedingungen herunterladbar sind, und
  das Ergebnis sagt, dass zugangsbeschränkte weggelassen wurden), `any` oder `gated`. `get_hf_model_info` zeigt ein
  Modell: seine Lizenz (`permissive` nur für eine Lizenz auf einer festen Positivliste wie `apache-2.0` oder `mit`;
  `restrictive` für bekannte einschränkende; sonst `unknown`, auch für `other`, eigene und listenförmige Lizenzen, bei
  `other` mit `license_name` und `license_link`), Lizenz und Zugangsbeschränkung des Basismodells (ein GGUF-Reupload
  eines zugangsbeschränkten Modells zeigt sich als solcher), die Zugangsbeschränkung (`false`, `auto`, `manual`), ob
  das im Framework gespeicherte Hugging-Face-Token es lesen kann, die Parameterzahl, den Commit, die Dateien mit
  ihren Größen und die Gesamtgröße. `download_hf_file` lädt eine Datei (eine GGUF-Datei, einzelne Gewichte, eine
  Tokenizer-Datei) als Job nach `models/hf/<owner>--<name>/<commit>/`: Bevor du gefragt wirst, legt das Framework
  Commit, Größe und sha256 fest, die der Hub nennt, lehnt ein zugangsbeschränktes Repository ab, das dein
  gespeichertes Token nicht lesen kann (mit dem nächsten Schritt), und prüft die Platte. Es gibt keine
  Größengrenze: Ein Download wird nur abgelehnt, wenn die Platte des Modellordners ihn plus Reserve (2 GiB oder 2 %)
  und das von anderen Downloads Reservierte nicht fasst; die Frage zeigt Größe, freien Platz danach, Lizenz und
  Zugangsbeschränkung. Der eigene Abrufer des Frameworks schreibt eine `.part`-Datei, die ein späterer Lauf
  fortsetzt, hält bei `cancel_job` zwischen zwei Blöcken an (der Teil bleibt) und gibt der Datei ihren Namen erst,
  wenn Größe und sha256 zu den Angaben des Hubs passen (eine kleine Datei ohne LFS ist über Größe und Commit
  festgelegt). Ein Download läuft zur Zeit, fünf am Tag je Client; Hugging-Face-Suchen sind auf 30 je Stunde
  begrenzt. Eine Daueranfrage kann GGUF-, safetensors- und kleine Textdateien nicht zugangsbeschränkter
  Repositories mit freizügiger Lizenz und nicht zugangsbeschränktem Basismodell abdecken, nie Pickle-Gewichte, und
  nur, solange die Platte nach dem Download die größere Reserve von 20 GiB oder 10 % frei behält (sonst wirst du
  gefragt). Jeder
  Hugging-Face-Aufruf für einen KI-Client sendet nur das im Framework gespeicherte Token (`llm-cli secrets set
  hf_token`) oder gar keins: nie eins aus `HF_TOKEN` oder dem Hugging-Face-Home-Ordner und nie an einen anderen Host
  als huggingface.co (eine Weiterleitung zum Dateispeicher des Hubs trägt kein Token). Das Token erscheint nie in
  Eingabe, Ergebnis, Hinweisen, Fehlern oder dem Audit-Log eines Tools. Im Offline-Modus lehnen diese Tools ab, und
  ein Netzwerkfehler ist ein Fehler, nie eine leere Liste. `list_local_files` listet die Modell- und Datensatzdateien
  in den Ordnern, die die KI nutzen darf (ein Hugging-Face-Modellordner als ein Eintrag, PyTorch-Pickle-Gewichte
  markiert, Links aus dem Ordner hinaus, mehrfach verlinkte und versteckte Dateien und unfertige Downloads
  weggelassen; 50 je Seite), jede mit Datei-ID und `<Label>:<relativer Pfad>`, die `start_build` annimmt; eine
  heruntergeladene Datei erscheint dort auch.
- **Hugging-Face-Downloads in der GUI (Entscheidung A6):** **Tools > Hugging Face Model
  Browser...** lädt über denselben Fetcher wie `download_hf_file` in denselben Ordner `models/hf/<owner>--<name>/<die
  ersten 12 Zeichen des Commits>/`: Zuerst wird der Commit nachgeschlagen, die Datei wird gegen Größe und sha256 des
  Hubs geprüft, bevor sie ihren Namen bekommt, und ein angehaltener Download setzt an seiner `.part`-Datei fort. Die
  Speicherplatz-Regel kommt zuerst (die Reserve bleibt neben dem frei, was laufende Builds und Downloads reserviert
  haben; sonst liest du, wie viel fehlt und was zu tun ist, in deiner Sprache), und der Download hält während er läuft
  eine eigene Reservierung. Das Fenster zeigt den Fortschritt und hat einen Knopf **Anhalten** (der bisher geladene
  Teil bleibt). Es sendet das Token, das du in seiner Token-Frage eingibst, sonst das im Framework gespeicherte, nie
  `HF_TOKEN`. Frühere Versionen speicherten die Downloads des Browsers im Cache von huggingface_hub im Modellordner
  (`models/models--<owner>--<name>/`); diese Modelle bleiben aufgeführt und für Builds nutzbar. **Alte Downloads
  übernehmen…** (freiwillig, einmal) kopiert sie in den neuen Ordner, jede Datei gegen die Prüfsumme geprüft, mit der
  sie geladen wurde (bei einem Cache ohne Links gegen das, was der Hub für diesen Commit führt); eine Datei, die nicht
  passt, oder eine andere Datei gleichen Namens im neuen Ordner bleibt, wo sie ist, mit ihrem Grund. Danach bietet er
  das Entfernen der alten Kopien im Löschdialog mit der genauen Liste an (der Cache-Ordner jedes Modells, dessen
  Dateien alle übernommen sind, und die Dateien des gemeinsamen Speichers des Caches, die nur sie nutzen, mit vollem
  Pfad und Größe; Papierkorb oder endgültig; „Nein“ ist die Vorgabe); ohne dein Ja wird nichts entfernt, und eine KI
  entfernt sie nie. Im Terminal macht `llm-cli models take-over` dasselbe, mit denselben Listen und Fragen. Im
  Offline-Modus nimmt der Browser zu niemandem Kontakt auf: Eine Suche, eine Lizenzprüfung, eine
  Dateiliste oder ein Download wird sofort abgelehnt, in klaren Worten, mit dem Ort, an dem du den Offline-Modus
  ausschaltest; die Übernahme funktioniert offline (eine Datei, die nur Hugging Face prüfen könnte, bleibt mit diesem
  Grund im alten Cache). Jeder Hub-Aufruf (Suche, Abfrage, Dateiliste) geht nur an huggingface.co, nie an den Host in
  `HF_ENDPOINT`, nie mit einem impliziten Token; dein eingegebenes oder gespeichertes Token geht nur an ein gated
  oder privates Repository (Dateien, Abfragen und Suchen eines öffentlichen gehen ohne).
- **Geräte und Deployment (`list_devices`, `create_deploy_package`, `deploy_to_device`):** `list_devices` zeigt die
  [vertrauten Geräte](#übertragen-auf-das-board-deployment) mit deinem Namen dafür, Adresse, Fingerabdruck des
  Host-Schlüssels, ob das Gerät nutzbar ist (gekoppelt und sein Schlüssel noch der vertraute) und dem letzten
  Deployment; das Konto, mit dem sich das Framework anmeldet, steht dort als "device account", nie mit seinem Namen.
  `create_deploy_package` paketiert ein gebautes Modell (Artefakt-ID) für das Geräteprofil eines Targets (Standard:
  das Target, für das es gebaut wurde) als Job; die Platte des Ausgabeordners muss das Doppelte des Modells fassen,
  und `include_docker_images` wird abgelehnt, solange nicht jedes Image per Digest
  festgelegt oder auf diesem Rechner gebaut ist. Das Ergebnis enthält die Paket-ID (`pkg_` und 16 Hex-Zeichen), Größe
  und sha256; eine Daueranfrage kann es nur ohne Docker-Images abdecken. `deploy_to_device` nimmt nur eine Paket-ID und
  eine Geräte-ID. Bevor du gefragt wirst, liest das Framework den Host-Schlüssel des Geräts (nur das Lesen des
  Schlüssels, keine Anmeldung) und lehnt einen unbekannten oder geänderten Schlüssel ab, ohne etwas zu schreiben
  (Grund `needs_person`); ein nicht gekoppeltes Gerät wird ebenfalls abgelehnt. Die Frage zeigt das Gerät mit deinem
  Namen dafür (seine Anmeldung, Konto und Adresse, nur im eigenen Fenster des Frameworks, nie in einem Formular, das
  eine KI lesen kann), den Fingerabdruck des Host-Schlüssels, den du bestätigt hast, Name, Größe und
  sha256 des Pakets, das gebaute Modell und das Vertrauen in sein Target. Das Deployment läuft dann als Job ohne
  Passwort und bricht ab, wenn das sha256 des Pakets (berechnet aus derselben geöffneten Datei, die hochgeladen
  wird) oder der Schlüssel des Geräts von dem abweicht, was dir gezeigt wurde. Es wird immer erfragt (es lässt sich
  nie auf „erlauben“ stellen), nie von einer Daueranfrage abgedeckt, läuft eins je Gerät zur Zeit und sechs je Stunde
  je Client. Bricht das Netzwerk weg, wartet der Job auf das Board (sein Fortschritt sagt „waiting for the board
  <Name>: N s of at most 300 s“) und setzt den Upload dort fort, wo er stehen blieb; `deploy.sh` wird nie zweimal
  gestartet. `cancel_job` schließt die SSH-Sitzung und beendet das Warten; sobald `deploy.sh` läuft, sagt der Job,
  dass der Zustand des Geräts unbekannt ist. Das Job-Ergebnis enthält `success`, `exit_status`, `device_state` (`ok`,
  `failed`, `unknown`), `reason_code` (einer der vier SSH-Grund-Codes oder leer), `resumed` und `waited_s` sowie die
  letzten 100 Zeilen, die das Gerät ausgegeben hat, in `external_text`, als externer Text markiert;
  `reason` ist immer der eigene Satz des Frameworks, ohne Pfade des Rechners. Scheitert ein Job der Geräte- oder
  Laufzeit-Tools, ist auch die Fehlermeldung der Satz des Frameworks: Was das Gerät oder `llama-bench` ausgegeben hat
  (`llama-bench` gibt die Metadaten des geladenen Modells aus), kommt in `external_text` zurück; Pfade in einer
  solchen Meldung erscheinen als Ordner-Bezeichnungen des Frameworks (`state:...`, `output:...`) oder nur als
  Dateiname, nie als Pfad des Rechners oder `~/...`, auch wenn ein Pfad Leerzeichen enthält (ein Pfad in
  Anführungszeichen, die Ordner des Frameworks und der Home-Ordner, auch unter seinem echten Pfad, werden als Ganzes
  ersetzt, auch weiter hinten in einer Pfadliste). Pfade von Linux und Windows werden auf jedem Rechner erkannt, mit
  beiden Trennzeichen und so, wie Python einen Windows-Pfad zitiert; der Home-Ordner eines anderen Kontos
  (`/home/<name>`, `C:\Users\<name>`) erscheint als „a home folder“, nie unter dem Namen des Kontos. Endet der
  Deploy-Job,
  bekommt das Audit-Log einen eigenen Abschlusseintrag (erledigt, gescheitert oder abgebrochen, mit Job, Gerätenamen
  und Gerätezustand), verknüpft mit dem `started`-Eintrag des Aufrufs; er wird auf jedem Weg aus dem Job geschrieben,
  auch wenn die Geräteliste das Deployment nicht eintragen kann (das Ergebnis hat dann eine `note`). Die Jobs von
  `create_deploy_package`, der Board-Tools und der lokalen Laufzeit-Tools schreiben ebenfalls einen solchen
  Abschlusseintrag (ok, Fehler mit seinem Grundcode oder abgebrochen; Geräte und Modelle unter ihren Bezeichnungen).
  Der Prompt `deploy_to_board` führt durch dieselben Schritte.
- **Warten und lange Operationen:** `get_build_status` und `get_job_status` nehmen `wait_seconds` (höchstens 50; ein
  größerer Wert wird mit einem Hinweis verringert) und kehren zurück, sobald sich der Build oder Job ändert oder
  endet. Das Warten läuft in der Ereignisschleife des Servers ohne eigenen Thread, höchstens 4-mal gleichzeitig je
  Client, und schickt alle 5 Sekunden eine Fortschrittsmeldung, wenn der Client Fortschritt angefordert hat. Ein
  Client, der ein Warten abbricht, stoppt nur das Warten, nie den Build oder Job. Lange Operationen außer Builds (aus
  den kommenden Tools für Crawlen, Herunterladen, Deployen und Freigaben) sind Jobs mit einer ID (`job_...`):
  `get_job_status` zeigt Zustand, Fortschritt und Ergebnis, `cancel_job` stoppt sie. Eine Zusammenfassung jedes Jobs
  (nie sein Ergebnis) bleibt in `~/.llm-framework/jobs/` für GUI und CLI.
- **Wem ein Build gehört:** Der Aufrufer, der einen Build oder Job startet, besitzt ihn zusammen mit seiner
  Verbindung: eine stdio-Sitzung (Akteur `mcp`, oder `mcp:<Name>` mit `LCF_MCP_TOKEN`) oder über HTTP jedes benannte
  Token. Eigene darf er ohne Frage abbrechen; einen Build oder Job eines anderen Clients abzubrechen folgt deinen
  Tool-Berechtigungen (standardmäßig fragt es dich). Dieselbe Identität des Aufrufers entscheidet über die
  Berechtigungen, steht im Audit-Log und zählt die 4 Wartevorgänge je Client. `get_build_status` zeigt den
  Aufrufer, der einen Build gestartet hat (`mcp`, `mcp:<Name>`, `ditto`).
- **Wenn ein Client die Verbindung trennt:** Die Builds und Jobs, die seine Sitzung gestartet hat, werden
  abgebrochen, jeweils mit einem Audit-Eintrag („client disconnected“); der Server wartet bis zu 30 Sekunden, bis
  ihre Container gestoppt sind, und fährt dann das Framework herunter, damit kein Build ohne Besitzer weiterläuft.
  Das gilt auch, wenn der Client einen stdio-Server mit SIGTERM beendet (wie Clients es tun, wenn er einige Sekunden
  nach dem Schließen seiner Eingabe nicht beendet ist). Über HTTP werden die Builds und Jobs jedes Tokens
  abgebrochen, wenn der Server endet, jeweils unter diesem Token protokolliert.
- **Transporte:** stdio (Standard; der Client startet den Server) oder Streamable HTTP nur auf einer
  Loopback-Adresse (`--transport http`, Standard `http://127.0.0.1:8765/mcp`). Über HTTP schickt jeder Client sein
  eigenes benanntes Token als `Authorization: Bearer <Token>`; eine Anfrage mit `Origin`-Header (eine Webseite, auch
  ein CORS-Preflight) wird abgelehnt, der `Host`-Header muss eine Loopback-Adresse mit dem Port des Servers nennen
  (gegen DNS-Rebinding), und der Server hält höchstens 8 Sitzungen, höchstens 4 je Token (damit ein Client die
  anderen nicht aussperren kann), schließt eine Sitzung nach 10 Minuten ohne Anfrage und nimmt höchstens 1 MiB pro
  Anfrage an. Er beachtet `X-Forwarded-For` und `X-Forwarded-Proto` nicht und schreibt kein Zugriffsprotokoll.
  Eine Sitzung, und eine offene Fragerunde des Protokolls 2026-07-28, gehört dem Token, das sie geöffnet
  hat (nach einer Rotation öffnet der Client eine neue Sitzung). Eine noch offene Anfrage (ein Warten, ein
  Ereignisstrom, ein Listen-Strom) wird geschlossen, sobald ihr Token widerrufen oder rotiert wird, und ebenso die
  Sitzungen dieses Tokens, sodass sie keinen Platz belegen. Was außerhalb einer vom Server geprüften Anfrage läuft,
  bekommt keinerlei Berechtigungen, über stdio wie über HTTP.
- **Benannte Tokens:** `llm-cli mcp token add NAME` legt ein Token für einen Client an und zeigt es einmal; das
  Framework behält nur seinen SHA-256 in `~/.llm-framework/mcp_tokens.json` (Modus 0600; solange andere Benutzer
  die Datei lesen oder ändern können oder sie einem anderen Benutzer gehört, wird kein Token angenommen und der
  Server startet nicht). Der Name des Tokens ist der Aufrufer im Audit-Log (`mcp:<Name>`),
  und es hat eigene Berechtigungen: Ein neues Token darf nur lesen, bis du mit
  `llm-cli tools permissions --token NAME` mehr erlaubst, es mit einer Kopie der Berechtigungen eines anderen
  Aufrufers anlegst (`--policy-from mcp`) oder es an Dittos Berechtigungen bindest (`--link ditto`, für einen
  Client, der genau Dittos Berechtigungen haben soll; eine Änderung an Dittos Berechtigungen gilt sofort auch
  dafür). `rotate` ersetzt ein Token,
  `revoke` löscht es mit seinen Berechtigungen; beides wirkt sofort, auch für einen laufenden Server, und die
  Aufrufe, die ein widerrufener stdio-Client noch schickt, werden abgelehnt und protokolliert. Ein
  stdio-Client bekommt sein eigenes Token über die Umgebungsvariable `LCF_MCP_TOKEN` in seinem Server-Eintrag:
  `llm-cli` nimmt sie aus seiner Umgebung, bevor irgendein Befehl läuft (kein Befehl und kein Kindprozess erbt sie),
  und gibt sie nur an `mcp serve` weiter, das nicht mit einem Wert startet, der kein gültiges Token ist. Ohne sie
  teilen sich alle lokalen stdio-Clients den Aufrufer `mcp` und deine eigenen Berechtigungen. Tokens verwaltet nur
  ein Mensch, an einem Terminal oder in der GUI (Tools > Tool Permissions (Ditto / MCP), Abschnitt „MCP-Tokens
  (eines je Client)“: auflisten, anlegen mit einmal gezeigtem Token, rotieren, widerrufen und die eigenen
  Berechtigungen jedes Tokens; protokolliert mit dem Akteur `gui`): Kein Tool kann sie anlegen, lesen oder ändern, und kein Token erscheint in
  einem Tool-Ergebnis, einer Logzeile oder einem Audit-Eintrag. Der Server liest den Token-Speicher bei jeder Prüfung
  und merkt ihn sich anhand des SHA-256 der Dateibytes, sodass er eine Änderung auch dann sieht, wenn sie Größe und
  Zeitstempel der Datei beibehält.
- **Arbeitsordner:** Der Server liest nie Einstellungen und importiert nie Code aus dem Ordner, in dem er gestartet
  wird (der Client startet ihn mit `python -P`), und unter Windows startet er von dort keine Programme wie `docker`
  oder `git`. Ein Projektordner kann sein Verhalten nicht ändern.
- **Client-Einrichtung:** `llm-cli mcp config` gibt die Befehle und das JSON so gequotet aus, dass sie in
  POSIX-Shells und in cmd wie PowerShell halten; `--write claude-desktop` trägt den Server in die Konfiguration von
  Claude Desktop ein, behält andere Einträge und schreibt eine private Sicherung.
- **Wer verbunden ist:** Für jeden neuen Client schreibt der Server eine Zeile nach stderr, nie an den Client: Name
  und Version, die der Client über sich selbst angibt, die Protokollversion, den Transport, den Aufrufer (`mcp` oder
  `mcp:<Name>` des benannten Tokens, nie das Token selbst) und die Fähigkeiten, die er erklärt hat, zum Beispiel
  `MCP client: "claude-code" 2.1.295 (self-reported), protocol 2025-11-25, stdio, caller mcp, elicitation form+url,
  capabilities: elicitation, roots`. `elicitation none` heißt, dass der Client dich nicht fragen kann; ein Tool auf
  „fragen“ wird dann zu einer wartenden Freigabe im Framework. Text vom Client wird zuerst von allem befreit, was wie ein Schlüssel oder Token
  aussieht, dann escapet und auf 64 Zeichen gekürzt; er ist eine Beschriftung, nie eine Berechtigung.
- **Test mit einem echten Client:** `scripts/mcp_client_smoke.sh` lässt Claude Code (`claude -p`) in einer
  Wegwerf-Umgebung gegen den Server laufen und prüft anhand des isolierten Audit-Logs, dass der Client festgehalten
  wird, `get_status` funktioniert, ein unbekanntes Argument mit Vorschlag abgelehnt wird, ein erklärter Alias
  festgehalten und in einem Ergebnis wie in einer Ablehnung gezeigt wird und ein Aufruf, der deine Bestätigung
  braucht, ohne Antwort nicht läuft. Er nutzt die eigene Anmeldung des claude-Befehls und rührt dessen Zugangsdaten
  nie an; ohne angemeldeten claude-Befehl wird er übersprungen. Siehe
  [den Smoke-Test mit einem echten Client](../CONTRIBUTING_DE.md#der-smoke-test-mit-einem-echten-client).

Was die Clients unterstützen, laut der Dokumentation ihrer Hersteller (2026-10-11). „Nicht dokumentiert“ heißt, dass
der Hersteller nichts dazu sagt; die stderr-Zeile oben zeigt, was ein Client wirklich erklärt.

| Client | Fragt dich (Elicitation) | Fortschritt | Ressourcen | Prompts | Protokoll 2026-07-28 |
|---|---|---|---|---|---|
| Claude Code (Laufzeit v1 und v2) | ja, Formular und URL | ja | ja | ja | Laufzeit v2 (Standard ab 2.1.232, ohne Feature-Flags ab 2.1.274) |
| Claude Desktop (Chat) | nicht dokumentiert | nicht dokumentiert | ja | ja | nicht dokumentiert |
| Claude Cowork | nicht dokumentiert | nicht dokumentiert | nicht dokumentiert | nicht dokumentiert | nicht dokumentiert |
| VS Code (GitHub Copilot) | ja, Modi nicht dokumentiert | nicht dokumentiert | ja, mit Vorlagen | ja | nicht dokumentiert |

Von Claude-Desktop-Chat und Cowork wird erwartet, dass sie keine Elicitation erklären (eine Quelle eines Dritten
sagt das): Dort wird jedes Tool auf „fragen“ zu einer wartenden Freigabe im Framework, erlaubte Tools
funktionieren.

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
| `external` | Andere Geräte erreichen oder Daten löschen | ask | ask, deny (ein Tool dieser Stufe fragt höchstens) |
| `forbidden` | Nie aufrufbar (zum Beispiel beliebige Shell-Befehle) | deny | deny |

Tools der Stufen `external` und `forbidden` gibt es noch nicht; Übertragen und Hochladen stehen nicht als Tools zur
Verfügung.

- Die Berechtigungen werden einmal abgefragt (GUI-Dialog beim ersten Start, `llm-cli ditto setup`) und in
  `~/.llm-framework/tool_policy.json` gespeichert (nur für dich lesbar). Einzelne Tools lassen sich in der GUI
  abweichend einstellen.
- Bis zur Einrichtung sind nur lesende Tools erlaubt, alles andere fragt.
- **Pro Aufrufer:** Ditto (`ditto`) und lokale MCP-Clients über stdio (`mcp`) können eigene Berechtigungen haben
  (`llm-cli tools permissions --caller ditto` oder `--caller mcp`); ein Aufrufer ohne eigene nutzt die gemeinsamen.
  Die Berechtigungen eines neuen Aufrufers beginnen mit den gemeinsamen, samt deinen Einstellungen für einzelne
  Tools. Ein benanntes Token für einen HTTP-Client (geplant) ohne eigene Berechtigungen darf nur lesen, und nie ein
  Tool, das deine gemeinsame Einstellung für dieses Tool oder die eigene Voreinstellung des Tools verbietet.
- **Eigene Vorgabe und Obergrenze eines Tools:** Ein Tool kann eine eigene Vorgabe mitbringen (ein optionales Tool
  beginnt mit `deny` und ist ausgeblendet) und eine höchste Entscheidung, die keine Einstellung anhebt (ein Tool, das
  andere Geräte erreicht oder Daten löscht, fragt höchstens). Die Reihenfolge ist: deine Einstellung für das Tool,
  sonst die Vorgabe des Tools, sonst deine Einstellung für seine Stufe; danach die Obergrenze. Die Vorgabe eines Tools
  öffnet nie eine Stufe, die du verweigert hast.
- **Neue Tools:** Die Berechtigungsdatei hält fest, welche Tools du gesehen hast; das Speichern der Einrichtung,
  `llm-cli tools permissions` oder der GUI-Dialog markiert jedes aufgeführte Tool. Ein später hinzugekommenes Tool
  nutzt seine eigene Vorgabe, nie eine lockerere Einstellung der Stufe, bis du es gesehen hast; `llm-cli tools list`
  und der GUI-Dialog kennzeichnen es als neu. Eine gespeicherte Berechtigungsdatei ohne diese Liste zählt die elf
  Tools von 3.0.0 als gesehen.
- **Verweigerte Tools werden nicht aufgeführt**, weder MCP-Clients noch Ditto; ein Aufruf eines solchen Tools wird
  trotzdem abgelehnt und festgehalten. `get_status` führt jedes Tool mit Entscheidung und Grund auf, auch die
  ausgeblendeten, und die Handlungen, die kein Tool übernimmt, mit dem Ort, wo du sie erledigst, damit eine KI dir
  den echten Grund nennen kann.
- Eine beschädigte oder unlesbare Berechtigungsdatei oder ein Link auf eine fehlende verbietet jedes Tool, statt auf
  lockerere Standardwerte zurückzufallen; eine beschädigte Datei bleibt als `tool_policy.json.bad` erhalten, und
  `llm-cli mojo`, `llm-cli tools list` und `llm-cli mcp serve` weisen darauf hin.
- **Nur du änderst die Berechtigungen.** `tool_policy.json` und der Token-Speicher `mcp_tokens.json` sind mit einem
  Schlüssel signiert, der aus dem Audit-Schlüssel in deinem Schlüsselbund abgeleitet ist. Eine von Hand bearbeitete,
  mit einem anderen Schlüssel signierte oder unsignierte Datei (auch eine von 3.0.0 gespeicherte Berechtigungsdatei)
  verbietet jedes Tool, und der Token-Speicher nimmt dann kein Token an, bis du die Berechtigungen neu einrichtest
  (`llm-cli tools permissions` oder der GUI-Dialog, der den Grund zeigt). Geändert werden sie nur in der GUI und mit
  `llm-cli tools permissions` und `llm-cli ditto setup`, die ein Terminal brauchen (weitergeleitete Antworten werden
  abgelehnt). Jedes Speichern wird im Audit-Log festgehalten (`policy_changed`, Akteur `cli` oder `gui`), mit den
  alten und neuen Entscheidungen, bevor es wirkt; lässt sich der Eintrag nicht schreiben, ändert sich nichts. Jedes
  Speichern erhöht eine Revisionsnummer unter einer Sperre, die GUI, CLI und MCP-Server teilen, und ein Speichern auf
  Grundlage einer älteren Revision (ein Fenster, das vor einer anderen Änderung geöffnet wurde) wird abgelehnt, damit
  es nie eine zwischenzeitliche Änderung rückgängig macht. Die neueste gespeicherte Revision beider Dateien steht
  zusätzlich im Schlüsselbund, ein Eintrag je Framework-Home (zwei `LLM_FRAMEWORK_HOME`-Ordner lehnen nie die Dateien
  des anderen ab): Eine ältere signierte Kopie, die über eine Datei zurückgelegt wird (sie brächte eine widerrufene
  dauerhafte Freigabe, ein widerrufenes Token oder eine lockerere Berechtigung zurück), wird wie eine Bearbeitung von
  Hand abgelehnt, auch wenn sie genau die Datei ist, die ein laufender Prozess vorher gelesen hat, über einen
  Hardlink zurückgelegt (unter Windows verrät das die eigene Änderungszeit von NTFS), und eine entfernte
  Berechtigungsdatei verbietet jedes Tool, statt auf die Vorgaben zurückzufallen. Die Builds, die du bestätigt hast,
  führen ihre Revision ebenso, daher zählt eine zurückgelegte ältere Kopie als keine Bestätigung. Ohne Schlüsselbund hat die Signatur keinen Schlüssel (wie die Audit-Kette) und `llm-cli mojo`
  warnt; eine solche Datei wird abgelehnt, solange das Audit-Log mit Schlüssel geführt wird.
- Ein [benanntes MCP-Token](#mcp-server) hat eigene Berechtigungen, gesetzt mit
  `llm-cli tools permissions --token NAME`; ein unbekanntes oder widerrufenes Token, oder eine Token-Datei, die sich
  nicht lesen lässt, darf nicht einmal lesen.
- **GUI:** Tools > Tool Permissions (Ditto / MCP)... zeigt je Tool, was jetzt gilt und warum („Gilt jetzt“, wie
  `llm-cli tools list`), nennt die Aufrufer mit eigenen Berechtigungen, bearbeitet die gemeinsamen Berechtigungen
  oder die von Ditto oder den lokalen MCP-Clients („Wessen Berechtigungen“, `--caller`) und setzt sie zurück: auf die
  empfohlenen Standards, einen Aufrufer zurück auf die gemeinsamen, ein Token zurück auf nur lesen (`--reset`,
  `--reset --caller`, `--reset --token`). Jedes Zurücksetzen nennt, was sich ändert, und fragt zuerst („Nein“ ist
  vorgewählt).

**Was eine Rückfrage zeigt** (Dittos GUI-Dialog und CLI-Abfrage, das Formular eines MCP-Clients), in dieser
Reihenfolge: die Folge in einfachen Worten, aus dem, was das Framework nachgeschlagen hat, mit IDs, denen ihr
sprechender Name folgt (zum Beispiel "Sets the framework setting build_jobs to 1 (fewer parallel compile jobs for
image builds), then retries build request req_1a2b3c4d (models:qwen/model.gguf for Rockchip (gguf Q4_K_M)) once."); wer
fragt (Ditto oder der MCP-Client mit dem Namen, der Version und dem Transport, die er angibt, und wessen
Berechtigungen gelten: alle lokalen stdio-Clients oder ein benanntes Token); die Fakten, die das Framework geprüft
hat, wobei Text von außerhalb des Frameworks (ein Build-Log, der Vorschlag einer KI) als "not checked" gekennzeichnet
und maskiert ist; dann jedes Argument vollständig, reparierte Werte markiert und eine ID mit ihrem sprechenden Namen
dahinter (Modell und Ziel einer Build-Anfrage, `<label>:<relativer Pfad>` einer lokalen Datei, der Name eines
Ziels). Manche Tools schlagen Fakten vor der Rückfrage nach (zweistufige Bestätigung), und der Aufruf ist dann
genau an das gebunden, was dir gezeigt wurde: Wer einen Selbstheilungs-Vorschlag genehmigt, wendet den Vorschlag aus
der Rückfrage an, und einer, der sich inzwischen geändert hat, wird abgelehnt, ohne dass etwas angewendet wird; die
Freigabe eines manuellen Schritts (auch einer vollen Docker-Platte, `docker_prune`: keine Selbstheilungs-Strategie
löscht etwas) wird abgelehnt, bevor etwas gefragt wird; ein Nachschlagen, das scheitert (kein offener Vorschlag), lehnt den Aufruf ab, bevor etwas gefragt
wird; ohne die nachgeschlagenen Fakten wird über einen Vorschlag gar nicht entschieden. Ein 2026-07-28-Client, der in
einer zweiten Runde antwortet, bekommt die Fakten der Rückfrage, die er beantwortet hat. Eine Runde zählt nur als
Antwort auf eine Rückfrage, die das Framework genau diesem Client gestellt hat und die noch offen ist: Eine Antwort,
die ein Client einem neuen Aufruf mitgibt, ist eine neue Rückfrage, wird festgehalten, bevor sie gestellt wird, und
unterliegt den Grenzen unten (nach deinem Nein wird sie nicht erneut gestellt). Ein MCP-Formular zeigt höchstens
4.000 Zeichen, die eigenen Fenster des Frameworks bis zu 16.000 (Dittos GUI zeigt eine lange Rückfrage in einem
scrollbaren Dialog, der dort, wo das Framework es kann, auch "nicht mehr fragen" anbietet, die CLI über den Pager);
über MCP wird eine Rückfrage zwischen 4.000 und 16.000 Zeichen zu einer ausstehenden Freigabe, und eine längere wird
überall abgelehnt (Grund `limit_reached`, mit einem nächsten Schritt). Eine ausstehende Freigabe ist dieselbe
Rückfrage: Sie zeigt die Fakten, die das Framework nachgeschlagen hat, und die sprechenden Namen, und das Framework
führt den freigegebenen Aufruf genau mit den Fakten der Rückfrage aus, die du freigegeben hast (ein Fakt, der sich vor
deiner Freigabe geändert hat, etwa ein neuer Upstream-Commit, fragt dich stattdessen erneut). Auch eine Dauerfreigabe
wird an diesen nachgeschlagenen Fakten gemessen, sodass ein Client, der dich fragen könnte, nicht fragt, wenn eine den
Aufruf abdeckt. Eine Rückfrage, die ein nachgeschlagener Fakt nötig gemacht hat, wird mit der Entscheidung `ask`
festgehalten, auch wenn das Tool selbst erlaubt ist. Wo das Framework "nicht mehr fragen" anbieten könnte, hat das
MCP-Formular ein weiteres Kästchen, das nur das Framework fragt: Es legt nie eine Dauerfreigabe an, und die Hinweise
des Aufrufs sagen das.

**Grenzen** (je Client: Ditto, die lokalen stdio-Clients, jedes benannte Token; gezählt, solange der Prozess
läuft): die lesenden Tools zusammen höchstens 20 Aufrufe in 10 Sekunden (40 auf einmal); `start_build` höchstens 3
unfertige Builds und 20 gestartete Builds in 24 Stunden je Client (ein abgelehnter oder verweigerter Start, etwa
wegen einer vollen Warteschlange, zählt nicht), und ein Framework-Prozess reiht höchstens 20 unfertige
Build-Anfragen von KI-Aufrufern ein (deine eigenen GUI- und CLI-Builds zählen zu den 20, werden aber immer
eingereiht: KI-Arbeit blockiert sie nie); höchstens 30 Rückfragen an dich pro Stunde, ausstehende Freigaben und
Bitten, nicht mehr zu fragen, eingeschlossen, und höchstens 5 offene ausstehende Freigaben je Client. Wenn du einen
ausstehenden Aufruf freigibst, werden seine Kontingente vor dem Ausführen noch einmal geprüft (die unfertigen Builds
des Clients können inzwischen mehr geworden sein). Nach deinem Nein, im Dialog des Clients oder im Freigabefenster,
wird derselbe Aufruf (dasselbe Tool mit denselben Argumenten nach der Reparatur, ein anders geschriebener
Aufruf ist also derselbe) 10 Minuten lang ohne Rückfrage abgelehnt, und drei Nein zu einem Tool innerhalb von 30
Minuten pausieren die Rückfragen dazu für 30 Minuten; ein geschlossener Dialog ist kein Nein. Ein Aufruf, den die
Wiederholungssperre abgelehnt hat (derselbe scheiternde Aufruf zum dritten Mal), darf 10 Minuten nach seinem letzten
Scheitern wieder laufen. Jede Grenze ist eine protokollierte Ablehnung (`rate_limited`, Grund `limit_reached` oder
nach deinem Nein `declined`), die sagt, wann es wieder geht, und sie zählt nie als gescheiterter Aufruf, für Ditto wie
für MCP-Clients. Dittos Chat hört nach drei gescheiterten Aufrufen in Folge ohne Fortschritt (jedes Mal dasselbe Tool,
derselbe Grund und dasselbe Feld; das Framework nennt das Feld, um das es bei einem Eingabefehler geht) oder einer
abgelehnten Wiederholung auf, Tools aufzurufen, und antwortet innerhalb von höchstens zwei weiteren Runden mit dem,
was er versucht hat, was gescheitert ist und warum; einen Eingabefehler nach dem anderen zu beheben ist Fortschritt,
und solange Ditto einen behebt, bekommt er bis zu 4 Schritte mehr als seine 8.

**Audit-Log** (`~/.llm-framework/audit.db`, SQLite): Jeder Tool-Aufruf wird mit Zeit, Akteur, Tool, Entscheidung, ob
ein Mensch bestätigt hat, und Ergebnis festgehalten. Argumente werden geschwärzt und gekürzt gespeichert, dazu ein
Hash des vollständigen Texts. Jeder Eintrag enthält den Hash des vorherigen; mit dem Schlüssel aus dem Schlüsselbund
deines Betriebssystems ist das eine HMAC-Kette, die jemand, der nur die Datenbank bearbeiten kann, nicht neu berechnen
kann. Nach jedem Eintrag wird der neueste zusätzlich in eine Anker-Datei geschrieben, sodass auch das Entfernen der
neuesten Einträge auffällt. `llm-cli tools audit --verify` prüft die Kette und unterscheidet einen geänderten Eintrag
von einem falschen Schlüssel. Jeder Eintrag wird in einer eigenen Transaktion geschrieben, damit mehrere Prozesse die
Kette nicht aufspalten. `llm-cli tools audit --archive` legt das Log und seinen Anker beiseite (mit Zeitstempel
aufbewahrt) und beginnt eine neue Kette, zum Beispiel nachdem `llm-cli mojo` ein Problem mit dem Log gemeldet hat.
GUI: Security > Prüfprotokoll... zeigt dieselben Zeilen Seite für Seite (mit oder ohne Details), prüft die Kette
(Unversehrtheit prüfen, das Ergebnis in einfachen Worten), archiviert das Log nach einer Rückfrage und zählt die
Aufrufe je Aufrufer; Prüfung, Archivieren und Zählen laufen im Hintergrund.

**Festgehalten, bevor er läuft.** Ein Aufruf, der etwas ändert (jede Stufe außer `read`), bekommt einen Eintrag
`started`, bevor er läuft. Lässt sich dieser Eintrag nicht schreiben, wird der Aufruf abgelehnt und nichts läuft:
Aufrufe, die etwas ändern, laufen nur, wenn sie festgehalten werden. Lesende Aufrufe bleiben verfügbar, und der
gescheiterte Schreibversuch wird protokolliert. Eine Frage an dich wird als `confirmation_requested` mit dem SHA-256
des Fragetexts festgehalten, bevor sie gestellt wird; eine Frage zu einer Änderung, die sich nicht festhalten lässt,
wird nicht gestellt. Jedes Ende wird festgehalten: `ok`, `error`, `denied`, `not_confirmed` mit der Antwort
(`accepted`, `declined`, `cancelled`, `unavailable`, wenn niemand gefragt werden konnte, `unanswered`),
`confirmation_timeout` (eine MCP-Frage während des Aufrufs wartet höchstens 5 Minuten; Grund `needs_person`, und eine
Frage, die der Client nicht zeigen konnte, hat den Grund `failed`, beide mit einem nächsten Schritt) und `cancelled`
(der Client hat den Aufruf aufgegeben). Eine Frage des Protokolls 2026-07-28, die an den Client zurückgeht und nie
beantwortet
wird, wird nach 15 Minuten als `unanswered` festgehalten, solange der Server läuft; der Eintrag
`confirmation_requested` ist der bleibende Nachweis. Bricht ein Client einen laufenden Aufruf ab, wird der Abbruch
sofort festgehalten und an den laufenden Aufruf weitergegeben; was schon läuft, endet, und sein Abschlusseintrag
folgt. Einträge enthalten außerdem den selbst angegebenen Namen und die Version des Clients (eine Bezeichnung, kein
Nachweis, wer er ist), einen Hash der Verbindung, den Bestätigungsweg (`elicitation`, `framework-gui`,
`framework-cli`), die Dauer, SHA-256 und Größe des Ergebnisses (nie das Ergebnis selbst) und einen Verweis auf den
Eintrag `started` des Aufrufs. Diese Felder sind an die Hash-Kette gebunden (ihr SHA-256 steht am Ende des
verketteten Detailtexts des Eintrags), so dass Hinzufügen, Entfernen oder Ändern erkannt wird, während ein Eintrag den
Hash behält, den 3.0.0 berechnet: Logs, die vor ihnen geschrieben wurden, bleiben prüfbar, und ein 3.0.0-Prozess, der
neben dieser Version noch läuft, prüft dasselbe Log weiter und schreibt hinein.

### Was das Framework vor einer Rückfrage prüft

Bevor dir ein Aufruf vorgelegt wird, der etwas ändert, schlägt das Framework die Fakten dahinter nach und ordnet, was
es findet, in drei Arten ein. Die Art jedes Befunds steht fest im Katalog des Frameworks; kein Tool und keine KI wählt
sie:

- **Harte Hindernisse** stoppen den Aufruf, bevor jemand gefragt wird, für eine KI genauso wie für dich: zu wenig
  Speicherplatz, der Offline-Modus, ein Board, das nicht gekoppelt ist oder dessen Identität sich geändert hat, ein
  Modell mit Bedingungen, das deine Hugging-Face-Anmeldung nicht öffnen kann, der noch nicht gelesene Hinweis zu Deep
  Ingest, ein zurückgezogenes Paket. Die KI bekommt den Grund und ihren nächsten Schritt; das Framework zeigt dir
  getrennt davon, was du tun kannst (etwa "Speicherplatz freigeben (alte Builds, den Modell-Cache, den Papierkorb)
  oder ein kleineres Modell wählen"). Wenn eine KI ein Hindernis selbst beheben kann (Dittos Modellserver ist noch
  nicht gebaut, ein Modell noch nicht heruntergeladen), nennt die Ablehnung das Tool, das es tut.
- **Situationshinweise** sind abzuwägen, aber nicht gefährlich: Der Speicherplatz wird knapp, reicht aber, ein großer
  Download, knapper Arbeitsspeicher für die Umwandlung oder zu wenig auf dem Board, viele Seiten für Deep Ingest, ein
  Hardware-Modul, das noch niemand auf dem Board ausprobiert hat, die Laufzeit auf einem Board wird ersetzt. Später
  darf eine KI, der du eine Vollmacht gibst, solche Hinweise selbst entscheiden. Die Zahl der Wissens-Snapshots ist
  nie ein Hinweis: Sie haben keine Grenze, und keiner wird von selbst gelöscht.
- **Sicherheits- und Rechtshinweise** bleiben immer bei dir: Modelldateien in einem alten Format, das versteckten Code
  ausführen kann (pickle), eine Build-Umgebung von außerhalb der geprüften Docker-Sammlung oder nicht auf eine genaue
  Version festgelegt, mehr Hersteller-Downloads als üblich, ein ungeprüftes oder ein Community-Hardware-Modul, ein
  nicht vertrauenswürdiges Modell, das direkt auf einem Board als dessen Gerätekonto läuft (oder in der Sandbox
  dieses Computers, außer du hast das für KIs mit der Einstellung `bench_untrusted_sandbox_ok` erlaubt, die keine
  KI ändern kann), ein Paket mit deinem
  Schlüssel signiert, ein Modell mit Bedingungen, eine Lizenz, die die Nutzung einschränkt oder nicht auf der Liste
  bekannter freier Lizenzen steht. Fakten, die sich nicht prüfen ließen (Hugging Face hat nicht geantwortet, keine
  Lizenz genannt, ein Basismodell, das nicht nachgeschlagen werden konnte, ein unbekanntes Dateiformat), sind
  ebenfalls ein Sicherheitshinweis: Unbekannt ist nie "kein Problem". Eine Build-Umgebung von außerhalb der geprüften
  Docker-Sammlung bleibt bei jedem Build ein Sicherheitshinweis, auch nachdem du einen Build damit einmal bestätigt
  hast (eine Dauerfreigabe, die du gewählt hast, kann diesen Build trotzdem abdecken).
- **Den ersten Build von Build-Code, den eine KI erstellt oder installiert hat, siehst du einmal selbst.** Ein Target-
  oder Rezept-Paket, das unter einer Dauerfreigabe (später auch einer Vollmacht) installiert wurde, und ein
  Hardware-Modul, das eine KI ohne dein Ja erstellt hat, tragen den Sicherheitshinweis
  "erster Build", bis du einen Build genau dieses Inhalts bestätigt hast (SHA-256 des Pakets oder Definition des
  Moduls); ein aktualisiertes Paket oder ein unter demselben Namen neu erstelltes Modul wird dir erneut gezeigt.

Eine Rückfrage im eigenen Fenster des Frameworks (Dittos Dialog, die CLI-Abfrage, das Freigabefenster) beginnt direkt
nach ihrer ersten Zeile mit einer Kurzfassung in deiner Sprache: was passiert, mit den Namen, die du kennst, wie viel
heruntergeladen wird und wie viel Speicherplatz nötig ist, "Braucht deine Entscheidung, weil: ..." mit jedem
Sicherheits- und Rechtshinweis, was abzuwägen ist, eine sicherere Wahl, wo es eine gibt, und wer entscheiden darf.
Die Kurzfassung entsteht nur aus Zahlen, die das Framework berechnet hat, den Hinweisen und den Namen, nie aus Text
aus einem Repository, einem Log oder von einer KI. Nach den Fakten nennt jede Rückfrage (auch ein MCP-Formular) die
Situationshinweise und getrennt davon die Sicherheits- und Rechtshinweise.

Eine ausstehende Freigabe wird nicht mehr erneut gefragt, wenn sich nur der freie Speicherplatz geändert hat, während
du sie gelesen hast: Eine Entscheidung ist an die Fakten gebunden, auf die es ankommt (die Anfrage, den Commit, die
Größenklasse, die Hinweise, die Image-Digests, das Board und seinen Host-Key), nicht an freien Speicher,
Byte-Zahlen oder die Uhrzeit, nach der ein Sicherungs-Snapshot benannt wird. Kommt ein Hinweis hinzu oder fällt einer
weg, etwa weil der Speicher jetzt knapp ist, wirst du erneut gefragt. Kurztexte in einer Rückfrage zeigen nie interne
Wörter des Frameworks: Was nicht geprüft werden konnte oder welche Hersteller-Liste gemeint ist, steht in klaren
Worten da. Unter einer Dauerfreigabe (und später einer Vollmacht) nutzt ein Build oder Download nur den Commit, den das
Framework nachgeschlagen hat; konnte Hugging Face nicht gefragt werden, startet nichts ohne dich.

**Board-Konten bleiben bei dir.** Das Konto, mit dem sich das Framework auf einem Board anmeldet (etwa `dietpi`), und
Pfade auf dem Board erreichen nie eine KI: `list_devices` sagt "device account", die Anmeldung (Konto und Adresse)
erscheint nur im eigenen Fenster des Frameworks, und alles, was ein Board antwortet (seine Ausgabe, Prüfergebnisse
und Fehler, auch wenn alles geklappt hat), erreicht eine KI ohne den Namen des Kontos, seinen Home-Ordner und
Board-Pfade.

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
  liegt nie in einer Git-Arbeitskopie, und ein beschädigter Tresor wird nie überschrieben. `llm-cli mojo` warnt
  vor der Schlüsseldatei und vor einem Schlüsselbund, der Geheimnisse nicht schützt. `llm-cli ditto auth
  rotate-key` ersetzt den Schlüssel des Tresors und verschlüsselt jeden Eintrag neu. GUI: **Security >
  Geheimnisse...** listet die Namen, die das Framework liest (`hf_token`, `qdrant_api_key`, die Schlüssel der
  Community-Pakete), und jeden gespeicherten Namen mit „gespeichert“ oder „nicht gespeichert“ und wofür (`secrets
  list`, nie ein Wert), und setzt jeden Namen mit dem Wert zweimal in verdeckten Feldern (`secrets set`; im Speicher
  `ephemeral` sagt es, dass nichts gespeichert wurde); **Security > Schlüsselspeicher...** zeigt und wechselt den
  Schutz und erneuert den Schlüssel des Tresors.
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
- **Einstellungen in der GUI:** **Datei > Einstellungen…** zeigt die wirksamen Einstellungen und woher jeder Wert
  kommt (die Vorgaben, eine Einstellungsdatei mit ihrem vollen Pfad, eine Variable `LLM_FRAMEWORK_*` oder während des
  Laufs geändert), Geheimnisse geschwärzt, wie `llm-cli config show` (`settings_view.effective_settings`), und die
  Repository-Überschreibungen mit der Variablen, zu der jede wird, zum Hinzufügen und Entfernen wie mit `llm-cli
  config repos` (Entfernen fragt zuerst, „Nein“ ist die Vorgabe). Eine Repository-URL mit Passwort oder Token wird
  in beiden mit derselben Prüfung abgelehnt: Zugangsdaten gehören in den Secrets-Speicher (**Security >
  Geheimnisse...**), nicht in die Einstellungsdatei aus reinem Text. Der Ausgabeordner hat seinen eigenen Eintrag,
  **Datei > Output Folder...**.
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
| Modell | Builder, **Source Model** (nach deinen zuletzt verwendeten Modellen: die Modellvorschläge des Quellenkatalogs, `llm-cli models suggestions`; jede andere ID oder jeder Pfad lässt sich eintippen) | das zuletzt verwendete | ein Build abgeschickt wird |
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
- **Tools > Eingabeverlauf löschen...** (englische Oberfläche: *Clear Input History...*) zählt zuerst die Datei und
  die `.bad`-Kopie auf (vollständiger Pfad, Dateiname, Größe, was es ist; „Nein“ ist die Vorgabe), löscht dann nur, was
  es aufgezählt hat, und die Felder werden sofort neu gefüllt.

---

## Fachbegriffe, Fehlermeldungen und Anrede

Fachbegriffe bleiben in beiden Sprachen Englisch, damit sie zu dem passen, was du anderswo findest; das Framework
erklärt sie dort, wo sie stehen.

- **Erklärungen beim Überfahren mit der Maus:** Lass die Maus auf einer markierten Beschriftung ruhen (der Mauszeiger
  zeigt ein Fragezeichen), und eine kleine Karte zeigt den Begriff, ein oder zwei einfache Sätze in deiner Sprache und
  einen Link auf die Seite, die ihn am besten erklärt: Wikipedia, die eigene Dokumentation des Begriffs (Qdrant, die
  Doku von llama.cpp und ggml, die RKLLM- und RKNN-Repositories von Rockchip, modelcontextprotocol.io) oder dieses
  Handbuch. Die Karte bleibt, solange die Maus auf der Beschriftung oder auf der Karte ist, damit du den Link erreichst;
  er öffnet sich erst in deinem Webbrowser, wenn du ihn anklickst. Mit der Tastatur drückst du **F1** im Feld (oder den
  kleinen Knopf **?**, wo ein Fenster einen hat); **Escape** schließt die Karte. Die Karte funktioniert auch in einem
  Dialog, der auf deine Antwort wartet. Screenreader lesen die Erklärung und den Link aus der Beschreibung des Felds,
  nach dem, was das Fenster selbst über das Feld sagt. Das Glossar hat 74 Begriffe (zum Beispiel GGUF, Quantization und ihre Typen
  wie Q4_K_M, Importance Matrix, Calibration Dataset, KV Cache, RKLLM, RKNN, NPU, Qdrant, MCP, SSH Host Key,
  Fingerprint, Digest, Keyring, Standing Approval); die Beschriftungen **Format** und **Quantization** im Builder
  nutzen es jetzt, die übrigen Fenster folgen. Ein Text, der dir sagt, wo du klickst, erklärt nur die Begriffe, die er
  selbst nutzt, nicht die Menütitel auf dem Weg.
- **Fehlermeldungen:** Ein Fehlerfenster sagt in deiner Sprache, was passiert ist, warum, wenn es bekannt ist, und
  einen nächsten Schritt (einen Satz, einen Knopf oder den Ort, an den du gehst, mit seinem Menüpfad und einem Knopf
  **Öffnen**). Die englischen technischen Details (die Meldung des Frameworks,
  ein Traceback) sind unter **Technische Details** eingeklappt; **Details kopieren** kopiert sie ohne Geheimnisse und
  mit `~` statt deines Home-Ordners, für einen Fehlerbericht oder um Ditto zu fragen, der sie dir auf Deutsch erklären
  kann. Kein Teil des Fensters zeigt ein Geheimnis, auch nicht dort, wo er die Meldung eines Fehlers wiederholt.
- **„du“ oder „Sie“:** Deutsche Texte sprechen dich mit „du“ an. **Datei > Einstellungen…** hat einen Reiter
  **Allgemein**, in dem du stattdessen „Sie“ wählst; die Wahl wird gespeichert (Einstellung `form_of_address`, `du`
  oder `sie`) und gilt sofort für jeden Text, der danach gelesen wird, auch für die nächsten Schritte und die
  Zusammenfassungen von Fehlern. Ein Fenster, das schon offen ist, behält seine Texte, bis du es neu öffnest.
- **Größen** erscheinen in Dezimaleinheiten in der Schreibweise deiner Sprache (`1,5 GB`, englisch `1.5 GB`); die
  Kommandozeile bleibt Englisch.

---

## Löschen und der Papierkorb

Nichts, was das Framework für dich löscht, wird ohne deine Frage gelöscht. Die Frage zählt genau
auf, was weggeht: zu jedem Teil den Namen, den vollen Pfad (bei einem Image die Docker-ID), den Dateinamen, die Größe
und was es ist. „Nein“ ist die Vorgabe. Eine KI-App oder Ditto sieht nie die Pfade, nur Namen, Arten und Größen, und
entscheidet nie über ein Löschen: nicht über ihr eigenes Formular, eine Dauerfreigabe oder eine Vollmacht.

- **Mehrfachauswahl.** Wo du aus einer Liste wählst (Wissens-Snapshots, Papierkorb, Build-Images des Frameworks), ist
  nichts angehakt. Das Framework baut die Liste aus genau den angehakten Teilen neu; die Schaltfläche nennt Anzahl und
  Größe. Hat sich eines davon geändert, während du die Liste gelesen hast, wird nichts gelöscht, und du siehst die
  neue Liste.
- **Der Papierkorb.** Ein Löschen verschiebt Dateien und Ordner zunächst in den Papierkorb: `.trash` im Datenordner,
  in dem sie liegen (der Ausgabeordner für gebaute Modelle, der persönliche Ordner für Snapshots). Du kannst sie
  wiederherstellen; ihr Platz bleibt belegt, bis du den Papierkorb leerst. Ein Verschieben auf eine andere Platte wird
  abgelehnt, nie kopiert. Wähle „Endgültig löschen“, um den Platz sofort freizugeben.
- **Nichts verlässt den Papierkorb von selbst.** Nach `trash_retention_days` (Vorgabe 14; eine KI kann es nicht
  ändern) erinnert der Papierkorb nur. **File > Trash...** in der GUI und `llm-cli trash` zeigen jeden Eintrag mit
  Art, Größe, Zeitpunkt, wer ihn angefordert hat und woher er kam. Dort stellst du Einträge wieder her oder löschst
  die angehakten endgültig. Kein Tool leert den Papierkorb.
- **KI-Apps.** `delete_artifact` verschiebt ein gebautes Modell nach deinem Ja im eigenen Fenster des Frameworks in
  den Papierkorb. Die optionalen Tools `list_trash` und `restore_from_trash` (verborgen, bis du sie freigibst) zeigen
  einer KI den Papierkorb mit Namen und stellen nur wieder her, was sie selbst löschen wollte; was du gelöscht hast,
  stellst du wieder her.
- **Keine KI erreicht die Papierkorb-Ordner.** Sie sind Ordner, die keine KI lesen oder einbinden darf, auch nicht
  über einen Link, und die Listen gebauter Modelle überspringen sie.
- **Das Audit-Log** hält jedes Löschen, Leeren und Wiederherstellen mit dem Hash der Liste fest, die du bestätigt hast
  (`deletion_sha256`), bevor etwas gelöscht wird; ohne Audit-Log wird nichts gelöscht.
- **Wissens-Snapshots** lassen sich auch exportieren: in einen Ordner deiner Wahl kopieren (auf diesem Computer, auf
  einen USB-Stick oder eine Netzwerkfreigabe). Jede Kopie wird per sha256 mit dem Original verglichen; ein Export
  löscht nie einen Snapshot.
- **Alte llama.cpp-Laufzeit-Builds und Geräte-Pakete** bleiben, wenn ein neuer gebaut oder deployt wird (vorher
  blieben nur die neuesten, ohne Liste). `llm-cli ditto runtime prune` und `llm-cli device runtime prune GERÄT`
  listen sie; der Build oder das Paket in Gebrauch steht nie in der Liste.
- **Was das Framework von selbst löscht**, ist nur seine eigene Buchführung, die du nie als Objekt siehst: temporäre
  und unfertige Dateien seiner eigenen Schreibvorgänge und Downloads, seine Staging-, Quarantäne- und Arbeitsordner
  eines Builds oder einer Installation, die Container, die es für einen Build gestartet hat, alte Einträge bis zu
  einer festen Anzahl (Build-Einträge, Anfragen, Jobs) und die Zustands- und Logdateien seines eigenen lokalen
  Modellservers. Ein Test listet jede Stelle im Code, die löscht.
- **Die Docker-Images des Frameworks** löscht das Framework nie von selbst. Der Builder behält das neueste Image jedes
  Targets als `llm-framework/<Target>:last-build` und entfernt nur den Namen des einzelnen Builds; ältere Images
  bleiben ohne Namen. **Tools > Framework Build Images...** in der GUI und `llm-cli doctor --fix --tier 2` listen
  jedes Image des Frameworks (Name, ID, Größe, was es ist) und löschen nur die angehakten. Nur auf diesem Weg löscht
  das Framework Docker-Images.

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
  Boards (nie root). GUI: Tab Builder > **Scan / Import...** führt dieselben Schritte aus wie die CLI: **Scan Local
  Host** (die Python-Probe, als `local` behalten), **Lokalen Rechner mit dem Ersatz-Skript scannen** (`--script`),
  **Lokalen Rechner scannen und Profil speichern unter...** (`--output`, eine `.json`-Datei für `--json`), **Lokalen
  Rechner mit dem Ersatz-Skript scannen und Profil speichern unter...** (`--script --output`), **Remote
  Scan via SSH...** (`--device`, mit **Port** und **Name**; dieselben Regeln für das Vertrauen beim ersten Kontakt wie
  beim Deployment, der Host-Schlüssel im Hintergrund gelesen, und das Board kommt wie über die CLI zu den vertrauten
  Geräten) und **Import from File (USB/Disk)...** (`--import`, fragt nach dem Namen des Boards). Eine Kopie jedes
  Ergebnisses legt sie für den Target-Abgleich in `<cache_dir>/probes/` ab. Der SSH-Scan der GUI nutzt dieselbe
  entfernte Probe wie
  `llm-cli scan --device`: Das Skript kommt über den SSH-Kanal, also funktioniert er auch auf Boards, die `/tmp` mit
  `noexec` einhängen, und das Profil wird unter der ID des Boards abgelegt. Bricht das Netz des Boards während des
  Scans ab, sagt das Protokoll, dass auf das Board gewartet wird und wann es wieder antwortet, und **Scan abbrechen**
  beendet den Scan, auch während dieses Wartens; dann wird nichts gespeichert. Rechner ohne
  Netzzugang führen eine Probe von Hand aus ([README](../README_DE.md#die-hardware-des-boards-untersuchen)).
- **Dittos lokales Modell** nutzt dieselbe Probe: Die Hardware, auf der Build- und Modellwahl beruhen, kommt aus einem
  Probe-Profil, und freier RAM und freier VRAM werden bei jedem Start neu gelesen.
- **Probes über ihre ID für KI-Clients und Ditto:** Jede Probe, die das Framework behält, hat eine Probe-ID: `local`
  für diesen Rechner, 16 Hex-Zeichen für ein Board (importiert mit `llm-cli scan --import DATEI` oder **Import from
  File** in der GUI, die die Datei jetzt auch geprüft und ohne Hostnamen ablegen, oder untersucht mit
  `scan --device`). Das Lese-Tool `get_hardware_probe` zeigt die neuesten Probes mit dem jeweils passenden Target
  oder die Werte einer Probe (Text von außen) und ihr Target; die Ressource `lcf://probes/{probe_id}` zeigt dasselbe.
  `probe_local_hardware` (fragt standardmäßig vorher) führt die Probe auf diesem Rechner als Job ohne Terminal aus, in
  einem eigenen Ordner, höchstens 120 Sekunden, und behält das Ergebnis als `local`. Kein Tool liest eine Probe über
  einen Pfad.
- **Neues Target von einem KI-Client (`create_target_module`):** Ein MCP-Client in Dittos Rolle, oder Ditto, legt ein
  Target-Modul für ein Board an, das hier vielleicht niemand testen kann, durch die Prüfungen des Modulgenerators.
  Nicht testen zu können ist kein Grund abzulehnen: Das Modul bekommt die Support-Stufe `community`, die Herkunft
  `ai-generated`, „untested“ in der Beschreibung und die Werte, die die KI geschätzt hat (`assumptions`), und Builds
  davon warnen, dass es ungetestet ist und sein Image mit Netzzugang gebaut wird. Jedes Basis-Image ist erlaubt: Ein
  Tag wird beim Nachfragen in den Digest aufgelöst, auf den es zeigt (anonym, im Offline-Modus abgelehnt; eine
  Registry, die eine Anmeldung verlangt, braucht von dir `image@sha256:...`), und das Modul wird mit genau diesem
  Digest geschrieben. Die Frage zeigt die Registry, „Docker Official Image“ oder klar „other registry; prebuilt image
  made by its publisher, not checked by the framework“, dass der Image-Build mit Netzzugang läuft, die Plattformen
  im Manifest des Images und ob eine zu diesem Rechner passt, die Begründung der KI für ein nicht offizielles Image
  (Pflicht), jede Hersteller-Paketquelle mit dem Fingerabdruck ihres Schlüssels, jedes festgelegte pip-Paket und
  jeden Download mit Adresse, Größe und sha256, und den aus der Probe des Boards (`probe_id`) geplanten
  `runtime:`-Block. Die Anzahl der Quellen (3), pip-Pakete (30) und Downloads (5) sind weiche Grenzen: Darüber zu
  gehen ist eine Warnung in der Frage, über die deine Zustimmung hinweggeht, nie eine Ablehnung. Das Tool fragt immer (auch
  wenn du es erlaubt hast), bietet nie „nicht mehr fragen“ an, lehnt einen Namen ab, der sich wie ein vorhandenes
  Target liest (Groß- und Kleinschreibung und Trennzeichen zählen nicht), und nimmt nie ein vorgefertigtes
  llama.cpp. Eine lange Definition wird zu einer ausstehenden Freigabe im Framework, mit bis zu 16.000 Zeichen
  angezeigt.
- **Neues Target per Fragen:** `llm-cli module create` oder der Assistent unter **Tools > Create Hardware Module...**,
  der auch nach der Beschreibung fragt (Vorgabe „Target for <Name>“). Nur du füllst sie aus: Bei einem Modul, an dem
  eine KI (über die GUI-Werkzeuge) mitgewirkt hat, steht deine Beschreibung vorn, und der Hinweis des Frameworks, dass
  das Modul KI-generiert und ungetestet ist, folgt. Nach **Finish** prüft der Assistent das neue Target wie `llm-cli
  targets validate` und zeigt das Ergebnis.
- **Neues Target aus einer Probe:** `llm-cli module generate-ai <Probe-Datei>` oder die KI-Analyse im Assistenten.
  Ditto schlägt Architektur, SDK, Basis-Image, Pakete, Hersteller-Paketquellen, pip-Pakete und Downloads, CPU- und
  CMake-Flags, Setup-Befehle (einmal in der Stufe source ausgeführt) und den Quantisierungsblock der Stufe convert
  (`convert_module.sh`) vor; du siehst zuerst den ganzen Vorschlag.
  Beide gehen denselben Weg: Das Basis-Image wird auf seinen Digest festgelegt, bevor du den Vorschlag siehst
  (`pin_base_image`; im Assistenten außerhalb des GUI-Threads), das Modul wird über `DittoManager.save_module`
  gespeichert, das ein vorgefertigtes `nvidia/cuda`-Image und ein Image, das kein Docker Official Image ist, ohne die
  Begründung der KI ablehnt (auch für ein Basis-Image, das eine KI über die GUI-Werkzeuge in den Assistenten eingetragen
  hat), und beide halten das gespeicherte Modul im Audit-Log fest (`module_saved`, mit dem sha256 der Definition und dem
  Basis-Image). Ein Basis-Image ohne Digest, das eine KI über die GUI-Werkzeuge eingetragen hat, wird bei **Finish**
  ebenso festgelegt (`pin_reference`, außerhalb des GUI-Threads): Der Assistent zeigt das festgelegte Image und fragt
  zuerst („Nein“ ist die Vorgabe); lässt sich der Digest nicht nachschlagen, wird nichts gespeichert. Der Assistent gibt
  jedes Feld des Vorschlags weiter. Seine erste Seite bietet Anbieter und Modell **nur
  für diese Analyse** (`--provider`, `--model`, `DittoManager.for_run`): Die gespeicherten KI-Einstellungen bleiben
  unverändert. Ein regelbasierter Import einer Probe nach Dittos Vorschlag ersetzt jeden Wert des Vorschlags (auch seine
  Pakete, Setup-Befehle und Quantisierungslogik), sodass das Modul dann deines ist.
- **Die Geräte-Laufzeit im Modul:** Jede neue `target.yml` bekommt einen Block `runtime:`, den Plan des Build-Rezepts
  für die llama.cpp-Laufzeit des Boards aus der Probe des Targets (die im Assistenten eingelesene Probe-Datei, die
  Probe-Datei von `module generate-ai` oder `llm-cli module create --probe <Datei>`): Architektur, Betriebssystem, die
  C-Bibliothek, die der Cross-Build nicht überschreiten darf, die Builder-Basis, die CPU-Optionen (für den RK3566
  `armv8.2-a+dotprod+fp16`), die Backends und die Programme. Er sagt, dass llama.cpp aus dem Quelltext gebaut wird;
  er ist eine Aufzeichnung, und die Laufzeit wird immer neu aus der eigenen Probe des Geräts geplant. CPU- und
  CMake-Flags aus dem Assistenten oder von Ditto stehen dort unter `advisory` und werden nie angewendet.
- **Die CMake-Flags des Moduls im Board-Paket:** Eine Allowlist entscheidet sie überall (Generator, Ordnerprüfung,
  Build-Plan, die letzte Prüfung des Pakets im Build-Container): `-DNAME=WERT` und CMakes `-W`-Wörter; was Code oder
  eine andere Toolchain lädt, `-S`/`-B`, `-C`/`-P` und Downloads werden abgelehnt. Eine gültige CMake-Option außerhalb
  der Liste (`-G Ninja`, `--trace`, ...) entscheidest du: Der Builder (oder `build start`) zeigt, was sie tut, und eine
  Empfehlung für das Modell und die Quantisierung, die du baust, und du erlaubst sie oder machst mit den
  Standard-Flags weiter; die Entscheidung lässt sich für die Modulversion merken. Ein von einer KI gestarteter Build
  mit so einer Option wartet auf dich
  ([Modul-Entwicklerhandbuch, Abschnitt 3.5](Module_Developer_Guide_DE.md#35-cmake-flags-eines-moduls)).
- **Abhängigkeiten vor dem Build geholt:** BoringSSL, LibreSSL und KleidiAI, die llama.cpp wegen einer CMake-Option
  eines Moduls beim Bauen herunterladen würde, werden stattdessen vor dem Build geholt: festgelegt, wie llama.cpp sie
  angibt, gegen die sha256 vom ersten Holen geprüft, schreibgeschützt in einem Abhängigkeits-Cache abgelegt und nur
  lesend in den Build eingehängt, der Build selbst bleibt also ohne Netz. Offline wird nur genommen, was im Cache ist.
  `build deps` und Build > Build-Abhängigkeiten… listen, holen und löschen sie.
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
  von den Deep-Ingest-URLs des Assistenten gecrawlt wurden, bereinigt nach `knowledge/initial_knowledge.json` im neuen
  Target, mit einer `README.md`, die sagt, was darin steht, und den Lizenztexten und der `NOTICE`, die seine Seiten
  brauchen (siehe [Lizenzen und Urheberrecht](#lizenzen-und-urheberrecht)). Solange ein Deep Ingest noch läuft, bleibt
  die Schaltfläche *Finish* des Assistenten gesperrt (die Seite sagt, warum), damit der Schnappschuss jede gecrawlte
  Seite enthält; stammt keine gespeicherte Seite von diesen URLs, sagt der Assistent, dass kein Schnappschuss
  angehängt wurde. Der Rest deiner Wissensbasis kommt nie in ein Modul, und `llm-cli module create` und
  `module generate-ai` hängen nichts an. Auf deinem Computer nimmt das Framework einen solchen Schnappschuss nicht auf
  (seine Seiten sind schon in deiner Wissensbasis); exportierst du das Target als Pack, nimmt das Framework des
  Empfängers ihn unter diesem Target auf (siehe [Wissen gehört zu einem Thema oder einem Target](#wissen-gehört-zu-einem-thema-oder-einem-target)).
- **Export für die Community:** Die letzte Seite des Assistenten bietet außerdem **Export for community after
  creating** an (siehe [Export für die Community](#export-für-die-community)).
- **Board-Empfehlung:** Nach einer Probe empfiehlt der Builder das Board des passenden Targets, mit der Angabe, woher
  die Empfehlung kommt und wie sicher sie ist: Der SoC der Probe heißt wie ein Board des Targets, oder der Board-Name
  der Probe steht in der Liste bekannter Boards des Targets (`examples` in `target.yml`). Nur ein genauer Treffer ist
  sicher und wird eingetragen; es ist nur eine Empfehlung, das Feld bleibt änderbar. Ist es nicht sicher, wird nichts
  eingetragen: Der Builder nennt die Kandidaten und warum es unsicher ist, und du wählst deins mit einem Klick. Bis du
  eins wählst, fragt **Start Build**, ob das Board im Formular deins ist („Nein“ ist die Vorgabe). **Board-Namen
  online nachschlagen...** öffnet deinen Webbrowser mit einer Suche nach dem Board-Namen der Probe, erst wenn du
  zustimmst. **Ditto fragen** schreibt eine Frage in den Chat, die du abschickst (ein Entwurf, den du dort getippt
  hast, bleibt). Ein Board, das du gewählt hast, wird weder durch eine spätere Probe noch durch eine KI ersetzt. Ein
  Target, das du gewählt hast (auch über ein Board, das du gewählt hast, oder das eines Rezepts), bleibt auch: Zeigt
  die Probe auf ein anderes Target, sagt der Builder das und bietet **<Target> verwenden** an, das dann das Board
  dieses Targets empfiehlt. Dass Ditto ein Board selbst über SSH untersucht, braucht das geplante Tool
  `probe_remote_device`.
- **Kein passendes Target:** Passt kein Target zur Probe, bietet der Builder **Target mit dem Assistenten erstellen**
  (der Assistent beginnt mit dieser Probe) und **Ditto fragen** an.
- **Dokumentations-Links für Deep Ingest:** Der Quellenkatalog (`configs/project_sources.yml`) enthält die
  Dokumentations-Links der Hersteller. Das Deep-Ingest-Fenster des Assistenten wird mit den Links für den Hersteller
  der Probe und das SDK auf der Hardware-Seite gefüllt; du prüfst sie und startest das Einlesen. Sagt Ditto, dass für
  das Modul Informationen fehlen, bietet der Assistent Deep Ingest mit diesen Links an. KI-Clients lesen sie über die
  Ressource `lcf://knowledge/doc-sources`; der MCP-Prompt `add_board` verweist auf sie, wenn Informationen fehlen.

Einzelheiten: [Module_Developer_Guide_DE.md](Module_Developer_Guide_DE.md).

---

## Gesundheitscheck (Mojo)

**Mojo** ist der Gesundheitscheck (er hieß doctor). `llmcf mojo` (dasselbe wie `llm-cli mojo`) führt
feste Prüfungen aus; nichts fragt ein Modell, was falsch ist. Die ganze Anleitung: [Mojo_DE.md](Mojo_DE.md).

| Bereich | Prüfungen |
|---|---|
| Python | unterstützte Version (3.11 bis 3.14) |
| Docker | Daemon erreichbar, buildx vorhanden; wo Docker seine Images ablegt (`DockerRootDir`, mit dem freien Platz dieser Platte) und wie viel die Build-Images des Frameworks belegen (Information) |
| Speicherplatz | freier Platz für den Cache-, den Ausgabe- und den Build-Ordner nach der einen Speicherplatz-Regel: ein Fehler, wenn weniger als die Reserve (2 GiB oder 2 % der Festplatte) frei bleibt, nach dem, was laufende Builds und Downloads reserviert haben, eine Warnung unter der doppelten Reserve |
| Targets | Targets gefunden, Capabilities lesbar |
| Sicherheit | persönlicher Ordner privat, Secrets-Speicher nutzbar, Schlüsselbund schützt Geheimnisse |
| KI | Tool-Berechtigungen eingerichtet und lesbar, MCP-Token lesbar, Audit-Log intakt (geändert, entfernt, falscher Schlüssel, fehlender Anker, kein Schlüssel); der Chat-Verlauf lässt sich schreiben und seine neuesten Einträge stimmen (geänderte oder fremde Einträge, kein Schlüssel) |
| Daten | mitgelieferte Kalibrierungsdaten intakt (Prüfsummen) |
| Community | installierte Community-Packs gegen den zwischengespeicherten Index: gesperrt (Fehler), seit der Installation verändert (Warnung), Vertrauensstufe `local` (Information) |
| Wissen | wie viele Snapshots der Wissensbasis es gibt, ihre Größe im Vergleich zum freien Platz ihrer Platte und ihr Ordner; eine Warnung, wenn der nächste Snapshot nicht mehr passen würde, eine Information, wenn er passt, aber wenig Platz übrig lässt (keine Grenze: ihre Zahl ist nie eine Warnung; nichts wird gelöscht); der Speicher der Wissensbasis, aus dem Eintrag ihrer Umwandlung in Qdrant Edge, ohne ihn zu öffnen (`knowledge.storage`): eine fehlgeschlagene Umwandlung (Warnung, mit dem Grund und wie du es erneut versuchst), Qdrant Edge, das sich für eine darin gespeicherte Wissensbasis nicht laden lässt (Fehler), der alte Speicherordner, der noch da ist (Information, mit vollem Pfad, Größe und wo du ihn löschst), der ältere Speicher auf einem Rechner ohne Build von Qdrant Edge (Information) |
| Laufzeit | der genutzte selbst gebaute llama.cpp-Build: veränderte Dateien (Fehler), älter als der Pin (Warnung), fehlt (Warnung); das vorgefertigte Archiv, als nicht selbst gebaut gekennzeichnet, genutzt oder installiert, aber nicht genutzt (Information); ob dieser Rechner die Werkzeuge für einen nativen Build hat (Information); im Docker-Modus Dittos Image: da, auf diesem Rechner gebaut, vom Pin und geprüft (OK), Docker nicht erreichbar oder das Image weg oder ein anderes (Warnung) |
| Boards | jedes vertraute Board, aus dem, was aufgezeichnet ist, ohne jeden Netzwerkkontakt: sein Host-Schlüssel in den `known_hosts` des Frameworks (geändert oder fehlt: Warnung) und das letzte Deployment, Laufzeit-Deployment, die letzte Prüfung, der letzte Benchmark oder die letzte Hardware-Prüfung, die es nicht erreicht haben (`device_host_down` Information; `device_port_closed`, `device_auth_failed`, `device_host_key_changed` Warnungen), mit dem nächsten Schritt und dem Angebot, es zu prüfen; `--board NAME` (oder `run_mojo_oracle` mit der Bezeichnung eines Boards) prüft ein Board sofort mit einer Verbindung zu seinem SSH-Port, ohne Anmeldung; nie das Gerätekonto |

**Mojo Oracle** (Standard) schaut nur. Zu jeder Warnung und jedem Fehler sagt es, was es gefunden hat, wo (den vollen
Pfad oder das Objekt), warum das wichtig ist, einen Hinweis und ob Mojo Miracle es reparieren kann; ein Satz am Ende
zählt jedes Problem danach, was sich tun lässt. Kann Mojo Miracle etwas tun, fragt es danach im Terminal „Run Mojo
Miracle now? [y/N]“; bei Ja läuft Mojo Miracle, ohne dass der Befehl noch einmal getippt wird.

**Mojo Miracle** (`llmcf mojo --miracle`) macht nur harmlose Reparaturen: Es schränkt den Zustandsordner auf deinen
Benutzer ein, oder es lässt Dittos Server einen intakten gespeicherten llama.cpp-Build starten, wenn der aktive fehlt.
Eine Reparatur braucht dein eigenes Ja: Installierte Wissens-Packs, die eine frühere Version ohne ihr Thema oder Target
importiert hat (`community.knowledge.untagged`), werden erst neu importiert (`knowledge.reimport`), nachdem Mojo
Miracle jedes Pack mit Ordner, Seiten und Größe gezeigt und du mit Ja geantwortet hast („Nein“ ist die Vorgabe), im
Terminal oder im Mojo-Fenster; zuerst wird ein Snapshot angelegt, und keine KI kann es starten (für eine KI gilt es
als Sache der Person).
Die Art einer Reparatur ergibt sich aus ihren Schritten (ein Schritt, der löscht, Zugriff erweitert, den Tresor, die
Berechtigungen oder das Audit-Log berührt oder das System braucht, ist nie harmlos). Jede Reparatur ist ein Probelauf,
an seinen sha256 gebunden; direkt bevor sie läuft, prüft Mojo noch einmal und ändert nichts, wenn das Problem weg ist
oder sich der Plan geändert hat. Das Audit-Log hält sie zuerst fest (sonst ändert sich nichts), die Änderung ist ein
Vergleichen-und-Tauschen am Ordner, der geöffnet wird, ohne einem Link zu folgen, es läuft immer nur ein Mojo Miracle,
und danach läuft die Prüfung erneut. Speicherplatz freigeben heißt löschen, also gilt nur deine Liste: Im Terminal
zeigt Mojo Miracle jedes Build-Image des Frameworks (die ohne Namen aus früheren Builds, die benannten Images
`llm-framework/<Target>:last-build` und die Builder-Images der Laufzeit; Name, Image-ID, Größe und was jedes ist),
nichts angehakt, und löscht genau die, deren Nummern du eingibst und bestätigst (Vorgabe Nein). **Tools > Framework
Build Images...** in der GUI ist dieselbe Liste. Der Docker-Build-Cache wird nie geleert, weil er sich nicht auf das
Framework beschränken lässt. Danach meldet Mojo Miracle jedes Problem: repariert (erneut geprüft), schon weg, noch da
oder nicht repariert mit dem Grund (Löschen braucht deine Liste, braucht dich, braucht einen Administrator, nicht
automatisch reparierbar, fehlgeschlagen); ein Problem, das die Prüfung danach zum ersten Mal findet, wird als neu
genannt. Ohne Terminal laufen nur die harmlosen Reparaturen, und nichts wird gelöscht. Den Speicherort der
Docker-Images auf eine andere Platte zu verlegen ist ein Schritt für Administratoren (siehe
[Container-Einrichtung](Container_Setup_DE.md#10-wo-docker-images-ablegt)); Mojo zeigt nur, wo er liegt. Exit-Codes:
0 OK, 1 Warnungen, 2 Fehler; `--json` für Skripte.

`llm-cli doctor` behält seine Optionen (`--fix`, `--tier 1|2`, `--json`), seine Ausgabe und seine Exit-Codes, mit
einem kurzen Hinweis, der `llmcf mojo` nennt; `--fix` macht dieselben protokollierten harmlosen Reparaturen, und
`--tier 2` zeigt auch die Liste der Build-Images des Frameworks.

GUI: **Tools > Mojo-Gesundheitscheck...** erledigt all das mit der Maus, über dieselben Funktionen
wie der Befehl. Mojo Oracle läuft beim Öffnen des Fensters, außerhalb des GUI-Threads, und zeigt jeden Befund so, wie
`llmcf mojo` ihn ausgibt (die Schwere als Text mit Zeichen und Symbol, nie nur als Farbe; wo, warum es wichtig ist,
den nächsten Schritt, was Mojo Miracle tun kann, mit seinem Plan; den zusammenfassenden Satz und eine Gesamtzeile mit
dem Exit-Code). **Mojo Miracle ausführen...** fragt „Mojo Miracle jetzt ausführen?“ (Nein ist die Vorgabe) und
repariert so, wie es der Befehl im Terminal tut: Die gezeigten Pläne binden die Reparaturen, die Build-Images des
Frameworks öffnen sich im Löschdialog mit der ganzen Liste und nichts angehakt, und das Fenster zeigt danach, was
repariert wurde, was nicht und warum. Das Fenster nimmt von sich aus keinen Kontakt zu einem Board auf: Ein Board, auf
das ein aufgezeichneter Fakt zeigt, bekommt **<Board> jetzt prüfen**, **Ein Board prüfen...** prüft ein beliebiges
vertrautes Board, das du auswählst, und **Dieses Board prüfen** im Deployment-Tab (Gruppe „Benchmark auf einem
Board“) prüft das gewählte Board; jede Prüfung ist eine Verbindung zum SSH-Port des Boards, keine Anmeldung.

Was eine KI liest: `run_mojo_oracle` liefert dieselben Befunde mit `where` als Bezeichnung, `why` und `repair` (`how`:
`miracle`, `deletion_list`, `person`, `admin` oder `not_automatic`, mit dem nächsten Schritt), die Ordner des
Frameworks unter ihrer Bezeichnung (`state:audit.db`, "the state folder", `targets:<Pack>`) und jeden anderen Pfad nur
mit seinem Dateinamen, auch in der Meldung einer abgestürzten Prüfung. `run_mojo_miracle` repariert ein Problem, dessen
`repair.how` `miracle` ist: Jeder Aufruf wird gefragt (der volle Pfad nur im eigenen Fenster des Frameworks), es löscht
nie, lehnt jedes andere Problem vor der Frage ab und sagt, ob repariert wurde und, wenn nicht, warum.
`run_mojo_oracle` nimmt keinen Kontakt zu einem Board auf; für ein Board, auf das ein aufgezeichneter Fakt zeigt, nennt
`check_board` die Anfrage, die es prüft, und `{"board": "<Bezeichnung>"}` prüft dieses eine Board sofort (eine
Bezeichnung oder Geräte-ID, nie eine Adresse). Der Prompt `check_installation` führt eine KI durch beides. `run_doctor`, der Name aus 3.0.0, bleibt aufrufbar, wird aber nicht
aufgelistet; eine Einstellung für einen der beiden Namen gilt für beide, und die strengere gewinnt. Deine eigene
Ausgabe (`llmcf mojo`, auch `--json`) behält die vollen Pfade.

---

## Community-Zoo

Der Community-Zoo teilt drei Arten von Packs: **Targets** (Hardware-Module), **Rezepte** (wie ein Modell für ein
Target gebaut wurde) und **Wissen** (Seiten für die Wissensbasis). Packs werden aus einem signierten Community-Index
aufgelistet, über einen Quarantäne-Ordner installiert und aus deiner eigenen Arbeit für einen Pull Request
exportiert. Befehle: `llm-cli zoo list|show|install|verify|export|keys`; in der GUI der
[Community Hub](#community-hub). Das Framework lädt nie etwas hoch und fragt nie nach einem Repository-Token, liest
oder zeigt keins.

**Stand heute:** Das Community-Repository ist festgelegt: Die Einstellung `community_repo` nennt standardmäßig
[Smilez1985/Edge-LLM-Knowledge-Base](https://github.com/Smilez1985/Edge-LLM-Knowledge-Base), und ein Pull Request geht
dorthin. Sein Beitrags-Workflow, sein signierter Index und der Maintainer-Schlüssel sind noch nicht veröffentlicht:
`configs/community_keys.yml` führt keinen Schlüssel, und die Einstellung `community_index_url` bleibt leer, bis in
diesem Repository der erste signierte Index liegt. Deshalb zeigen `llm-cli zoo list` und der Hub nur installierte Packs,
und installieren lassen sich nur Pack-Dateien (Vertrauensstufe `local`).

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
  Schlüssel bekommt in der Zusammenfassung eine Warnung. Ein beschädigter Speicher der festgehaltenen Schlüssel
  (`authors.json` im Cache-Ordner der Community) wird nie für einen leeren gehalten: Er bleibt als `authors.json.bad`
  erhalten, die Zusammenfassung warnt, dass der Schlüssel nicht verglichen werden konnte, und kein Schlüssel wird
  festgehalten, bis du die Datei reparierst oder entfernst, sodass die bisherigen Einträge nie überschrieben werden.

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
  beide (GUI: ein Feld für die Passphrase, die nie gespeichert oder angezeigt wird, und **Nur diese Absender
  annehmen** mit Schlüsseldateien); **Meine öffentlichen Schlüssel...** im Hub zeigt deine öffentlichen Schlüssel mit
  **Kopieren** und **Speichern...**. Ein verschlüsseltes Pack wird nie an die Community übergeben.

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

Targets kommen nach `targets/`, Rezepte nach `community/recipes/`, Wissen in den Ordner seines Themas oder Targets
in `community/knowledge/` (siehe [Wissen gehört zu einem Thema oder einem Target](#wissen-gehört-zu-einem-thema-oder-einem-target)).
Ein Wissens-Pack wird direkt nach seiner Installation in die Wissensbasis aufgenommen oder, wenn die Wissensbasis
ausgeschaltet ist, sobald sie eingeschaltet wird. Bevor seine erste Seite aufgenommen wird, wird die Wissensbasis als
Schnappschuss gesichert (`pre_pack_<Zeit>`), damit sich der Import rückgängig machen lässt. Passt
dieser Schnappschuss nicht mehr auf die Platte, wird nichts aufgenommen: das Pack bleibt installiert und wird
aufgenommen, sobald Platz da ist. Ein Pack, das sich noch nicht aufnehmen lässt (zum Beispiel offline ohne das
Embedding-Modell), wird beim nächsten Start ohne weiteren Schnappschuss noch einmal versucht; ein erneut installiertes
Pack bekommt einen neuen.

### Wissen gehört zu einem Thema oder einem Target

Jedes geteilte Wissen gehört zu einem Thema (zum Beispiel
`llama.cpp`) oder zu einem Target (zum Beispiel `Rockchip`), und so wird es auch auf der Platte und in der Wissensbasis
gehalten:

- **Ordner:** Ein installiertes Wissens-Pack liegt in `community/knowledge/<Thema oder Target>/<Pack>/` mit seiner
  `.lcf-provenance.json` (ein Pack, das eine frühere Version installiert hat, bleibt in `community/knowledge/<Pack>/`
  und funktioniert weiter). Ein Pack nennt sein Thema oder Target im Manifest; ein Pack, das erstellt wurde, bevor
  Packs das taten, bekommt seine ID als Thema, was die Zusammenfassung der Installation sagt und deine Bestätigung
  annimmt (keine dauerhafte Freigabe einer KI deckt das ab).
- **Keine losen Dateien:** Eine `.json`-Datei direkt in `community/knowledge/` oder in einem Themenordner, oder eine
  `knowledge.json` ohne die Herkunftsdatei des Packs, wird nie importiert. `llm-cli knowledge loose`, Mojo und **Lose
  Wissensdateien einsortieren...** im Community Hub listen sie; eine davon in ein Thema oder Target einzusortieren
  prüft sie wie ein installiertes Wissens-Pack (in der Quarantäne: eine Liste von Seiten, jede mit ihrer Quelle; die
  Lizenztexte und die `NOTICE`, die ihre Volltext-Seiten brauchen), zeigt die Zusammenfassung mit „Nein“ als Vorgabe
  und verschiebt sie erst dann in den Ordner (Vertrauensstufe `local`) und importiert sie. Der vorläufige
  Wissens-Export des Modul-Assistenten landet in einem privaten Arbeitsordner, nie in `community/knowledge/`. Ein Pack,
  dessen Dateien sich nach der Installation geändert haben, wird ebenfalls nicht importiert; warum ein Pack wartet,
  wird nach der Installation gesagt, nicht nur ins Log geschrieben.
- **Auszüge:** Jeder Auszug eines Packs trägt sein Thema oder Target; eine Seite des Packs kann es nicht setzen, auch
  nicht das Pack, aus dem sie angeblich stammt. `search_knowledge` und `list_knowledge_sources` nehmen `topic` (ein
  Thema oder ein Target), `llm-cli knowledge sources --topic` und das Feld **Thema oder Target** in **Tools >
  Wissensquellen...** grenzen die Liste genauso ein. Deine eigenen gecrawlten Seiten gehören von Anfang an zu einem
  Thema: Das Deep-Ingest-Fenster fragt danach (leer: der Name der Site, etwa `docs.example.com`), und ein Crawl, den
  eine KI gestartet hat, nimmt den Namen der Site. Auszüge, die eine frühere Version importiert hat, bevor Auszüge ihr
  Thema oder Target trugen, findet `topic` nicht, bis ihr Pack neu importiert ist: Ist die Wissensbasis offen, nennt
  Mojo Oracle diese Packs (`community.knowledge.untagged`), und Mojo Miracle importiert sie erst nach deinem eigenen
  Ja neu, nachdem es jedes Pack mit seinen Seiten und seiner Größe gezeigt hat (zuerst wird ein Snapshot angelegt;
  eine KI kann es nicht starten).
- **Ersetzen:** Eine neuere Version eines Packs ersetzt die Auszüge der älteren, je Pack und Seitenquelle: Sobald jede
  Seite der neuen Version drin ist, werden die Auszüge entfernt, die die alte Version hatte und die neue nicht, sodass
  kein veralteter Auszug bleibt. Der Snapshot der Wissensbasis vor dem Import bleibt. Eine Seite, die du mit Deep
  Ingest erneut crawlst, ersetzt ebenfalls ihre früheren Auszüge (jede gecrawlte Seite wird direkt nach dem Download
  gespeichert), und ein Crawl derselben Startadresse, der die ganze Site gelesen hat, entfernt die Auszüge von Seiten,
  die es nicht mehr gibt (ein teilweiser oder fehlgeschlagener Crawl entfernt nichts, ebenso ein Crawl, dessen
  Tiefengrenze oder Sprachfilter Links ungelesen ließ: die Seiten dahinter sind nicht weg, nur nicht erreicht).
- **Geteilte Seiten:** Zwei installierte Packs, oder ein Pack und dein eigener Crawl, mit derselben Seite teilen sich
  ihre Auszüge; jeder Auszug nennt seine Halter. Eine neuere Version eines Packs, ein neuer Crawl oder eine
  Deinstallation gibt nur den eigenen Halt auf, sodass eine Seite, die ein anderes Pack noch hält, bleibt (und unter
  dessen Thema oder Target gefunden wird).
- **Target-Wissen:** Der Wissens-Schnappschuss, den ein Target-Pack mitbringt (`knowledge/initial_knowledge.json`, vom
  Modul-Assistenten erstellt), wird bei der Installation wie ein Wissens-Pack geprüft und unter diesem Target in die
  Wissensbasis des Empfängers importiert, mit einem Snapshot vorher und derselben Ersetzung durch eine neuere Version.
  Auf dem Computer, der das Target erstellt hat, wird nichts importiert: seine Seiten sind schon in dieser
  Wissensbasis.
- **Exporte:** `llm-cli zoo export knowledge` braucht `--topic` oder `--target` (ein Target dieses Computers), das
  Feld **Gehört zu:** im Export-Fenster und das Werkzeug `export_pack` nehmen dasselbe.

### Vertrauensstufen

| Stufe | Bedeutung |
|---|---|
| `verified` | im signierten Index: geprüft und unabhängig nachgebaut |
| `community` | im signierten Index: geprüft |
| `local` | eine Datei ohne Eintrag im signierten Index: niemand hat sie geprüft. Die Installation fragt immer, und jeder Build eines solchen Targets zeigt eine Warnung. |
| `revoked` | in der Sperrliste des signierten Index: Die Installation ist blockiert. Installierte Kopien werden von `llm-cli zoo list`, `zoo verify`, `llm-cli mojo` und dem Hub markiert und nicht gebaut, auch nachdem der Schlüssel abgelaufen ist, der die Sperre signiert hat. |

Targets, die mit dem Framework ausgeliefert werden, etwa Rockchip, haben keine Herkunftsdatei; sie sind keine Packs
und haben keine Vertrauensstufe.

### Installierte Packs prüfen

- `llm-cli zoo verify` prüft jedes installierte Pack gegen den zwischengespeicherten Index: gesperrt oder seit der
  Installation verändert. Der Exit-Code ist 1, wenn es ein Problem findet. GUI: **Tools > Community Hub öffnen >
  Installierte Pakete prüfen**.
- `llm-cli zoo verify <Pack-Datei>` prüft eine Datei, ohne sie zu installieren: Format, Hashes, die Prüfungen ihrer
  Art, eine enthaltene Signatur des Autors und die Datenschutz-Bereinigung (keine Home-Ordner, Adressen, Host- oder
  Benutzernamen, Schlüssel). Es braucht keinen Schlüsselbund; die CI des Community-Repositorys nutzt es. GUI:
  **Tools > Community Hub öffnen > Paketdatei prüfen...**, bevor du eine Datei installierst oder weitergibst.
- `llm-cli mojo` meldet gesperrte Packs als Fehler, veränderte Packs als Warnung und Packs mit der Vertrauensstufe
  `local` als Information ([Gesundheitscheck](#gesundheitscheck-mojo)).

### Community Hub

**Tools > Community Hub öffnen** (englische Oberfläche: *Open Community Hub*) listet die Einträge des Index und die
installierten Packs mit Name, Art, Version, einer farbigen Spalte für die Vertrauensstufe, Autor und Status, mit
einem Suchfeld und Filtern nach Art und Vertrauensstufe. **Refresh** holt den Index neu, **Install** installiert einen
Eintrag des Index nach seiner Zusammenfassung, **Import file...** installiert eine Pack-Datei (auch eine, die für
deinen Schlüssel verschlüsselt oder mit einer Passphrase geschützt ist, und nur von den Absendern in
Schlüsseldateien, wenn du sie wählst), und **Build** bei einem installierten Rezept füllt den Builder aus. Ein
gesperrtes Pack zeigt eine abgeschaltete Schaltfläche **Revoked**. Ein installiertes Target wird wie mit `llm-cli
targets validate` geprüft, und das Ergebnis wird angezeigt. **Details...** (oder ein Doppelklick auf eine
Zeile) zeigt, was `llm-cli zoo show` zeigt: Lizenz, Vertrauensstufe und Sperre, die installierte Version und den
Ordner, die Download-Adresse mit Größe und sha256, die Beschreibung und die `recipe.yml` eines installierten Rezepts.
**Installierte Pakete prüfen**, **Paketdatei prüfen...**, **Exportieren...** und **Meine öffentlichen Schlüssel...**
tun, was `zoo verify`, `zoo verify DATEI`, `zoo export` und `zoo keys` tun; jedes läuft im Hintergrund.
**Deinstallieren...** bei einem installierten Pack tut, was `llm-cli zoo uninstall` tut: Das Löschfenster listet seinen
Ordner, seine Import-Notizen und die Auszüge, die nur es hält, „Nein“ ist die Vorgabe, und es geht in den Papierkorb
oder endgültig; Auszüge, die ein anderes Pack noch hält, bleiben. Eine KI darf nur darum bitten (das optionale Tool
`remove_pack`), und du entscheidest im selben Fenster. Holst du es aus dem Papierkorb zurück, werden seine Seiten
beim nächsten Wissensimport erneut importiert.

### Export für die Community

- **Wo:** auf der letzten Seite des Modul-Assistenten (**Export for community after creating**: das neue Target), im
  Kontextmenü des Builder-Logs (**Export a finished build for the community...**: die `build_info.json` eines
  fertigen Builds wird zum Rezept) und mit `llm-cli zoo export target <ID>`, `zoo export recipe <Build-Ordner>` oder
  `zoo export knowledge`, jeweils mit `--author <Name>` (ein Pseudonym genügt). GUI: **Tools > Community Hub
  öffnen > Exportieren...** exportiert jeden Target-Ordner (auch einen vorhandenen oder installierten; ein
  Community-Pack wird abgelehnt, siehe unten), einen fertigen Build oder die Wissensbasis, mit der Paket-ID und dem
  Thema oder Target eines Wissens-Packs (**Gehört zu:**, `--topic` oder `--target`, Pflicht), privatem Teilen an die
  Schlüssel in Schlüsseldateien oder mit einer Passphrase, und dem Ordner.
- **Datenschutz-Bereinigung:** Jede Datei wird auf diesem Rechner bereinigt. Home-Ordner werden zu `~`, IP- und
  MAC-Adressen zu `<ip>` und `<mac>`, E-Mail-Adressen zu `<email>`, JWTs zu `<jwt>`, der Hostname zu `<host>`, der
  Benutzername zu `<user>`, und API-Schlüssel werden geschwärzt. Loopback-Adressen, Image-Digests, `git@`-Remotes und
  Standardkonten wie `root` oder `runner` bleiben, damit ein bereinigtes Target weiter baut. Die Änderungen werden als
  Diff gezeigt, bevor du etwas teilst. Binärdateien werden abgelehnt.
- Das Pack wird wie bei einer Installation geprüft und mit deinem Schlüssel signiert. Es wird nach
  `community/contributions/` geschrieben (oder nach `--out`).
- **Lizenz:** standardmäßig MIT für dein eigenes Target oder Rezept (`--license`; in der GUI eine Liste).
  Wissens-Packs enthalten Seiten anderer Leute: Ihre Lizenz ist `NOASSERTION`, jede Seite behält ihre Quelladresse,
  und eine Seite ohne Quelle wird nie exportiert. Der volle Text einer Seite kommt nur hinein, wo ihre Lizenz die
  Weitergabe erlaubt; jede andere Seite kommt als ihre Adresse mit kurzen Fakten hinein, und die Zusammenfassung führt
  jede Seite auf, mit dem, was sie enthält, und warum. Seiten mit vollem Text unter einer Lizenz, die ihren Text
  verlangt, bekommen diesen Text in `LICENSES/` und eine `NOTICE`, und die Zusammenfassung nennt jeden Text und warum
  (siehe [Lizenzen und Urheberrecht](#lizenzen-und-urheberrecht)).
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
  pack to the public community repository?“, außer `--yes` ist angegeben; ohne Terminal wird nichts übergeben. Das
  Export-Fenster stellt nach seiner Zusammenfassung dieselbe Frage („Nein“ ist die Vorgabe).
- Sind die GitHub CLI (`gh`) und `git` installiert und ist `gh` angemeldet, forkt das Framework das Repository,
  committet das Pack als `contributions/<Art>/<Datei>` auf einem neuen Branch und öffnet den Pull Request mit
  `gh pr create`; `gh` und `git` nutzen ihre eigenen gespeicherten Zugangsdaten. Sonst, oder wenn das scheitert,
  öffnet sich die Upload-Seite des Repositorys im Browser: Leg die Datei dort ab, und GitHub forkt das Repository für
  dich.
- Der Standardwert von `community_repo` ist das Community-Repository, `Smilez1985/Edge-LLM-Knowledge-Base`. Ist die
  Einstellung geleert, wird nichts übergeben; das Pack liegt fertig in seinem Ordner.

### Packs für KI-Clients

MCP-Clients und Ditto nutzen den Zoo mit drei Tools (Entscheidungen vom 11.10.2026): Packs kommen nur aus dem
signierten Index und verlassen diesen Rechner nur als lokale Datei, die du selbst veröffentlichst.

- **`list_packs`** (lesen): die Einträge des Index und die installierten Packs mit Art, ID, Version, Titel, Autor,
  Lizenz, Vertrauensstufe (`verified`, `community`, `local`, `revoked`), installierter Version und dem Grund eines
  Rückzugs, bei einem Wissens-Pack mit dem Thema oder Target, das sein Indexeintrag nennt (`topic`, `target`). Es
  liest den zwischengespeicherten Index; `refresh` lädt den signierten Index neu (einmal pro Stunde je Client; nicht
  im Offline-Modus). `pack_id` ergänzt die Beschreibung eines Packs, Downloadgröße und sha256 und bei
  einem installierten Rezept seine festgelegten Werte; `verify` ergänzt die Prüfung der installierten Packs
  (zurückgezogen, seit der Installation geändert). Titel, Autoren, Beschreibungen und Rezepte sind externer Text;
  Ordner erscheinen nur als Labels.
- **`add_pack_from_index`** (extern, immer gefragt): ein Wissens-, Target- oder Rezept-Pack aus dem signierten Index
  mit Vertrauensstufe `verified` oder `community`, als Job. Nie ein zurückgezogenes Pack, nie ein Pack aus einer Datei
  und nie ein verschlüsseltes (die bleiben im Community Hub und bei `llm-cli zoo install --file`). Bevor du gefragt
  wirst, lädt das Framework den aktuellen Index, prüft den Speicherplatz (die doppelte Pack-Größe plus Reserve), lädt
  das festgelegte Pack in die Quarantäne und prüft es; eine Ablehnung nennt ihren Grund. Die Frage zeigt das Pack mit
  Autor und Lizenz (als Text aus dem Index markiert), die Vertrauensstufe, die sha256, die Dateien, wohin es kommt
  (`targets/<id>`, `community/recipes/<id>`, `community/knowledge/<Thema oder Target>/<id>`), sein Thema oder
  Target, die Hinweise des Packs und bei einem
  Target-Pack, dass es Build-Skripte und ein Dockerfile hinzufügt, dessen Image-Build mit Netzzugang läuft, mit jedem
  Image, das ein Dockerfile bezieht (`FROM`, `COPY --from=`, `RUN --mount=...,from=`), klar benannt: Registry, Image, Tag, Digest oder „not pinned by digest“ und „Docker
  Official Image“ oder „other registry: ...; prebuilt image made by its publisher, not checked by the framework“.
  Der Job installiert genau die Kopie aus der Quarantäne mit der gezeigten sha256. Die Kopie wird wiederverwendet,
  solange nach demselben Pack und Hash gefragt wird, und entfernt, wenn du nein sagst, wenn eine wartende Freigabe
  abgelehnt wird oder abläuft, oder nach 20 Minuten ohne Nutzung. Ein Target-Pack, dessen ID ohne Rücksicht auf
  Groß- und Kleinschreibung und Trennzeichen der eines vorhandenen Targets gleicht (`my_board` neben `My-Board`), wird
  abgelehnt, auch im Community Hub und in der CLI. 5 Importe am Tag je Client.
- **„Ja, und nicht mehr fragen“** für Packs, nur im eigenen Freigabefenster des Frameworks: Es gilt für eine Pack-Art,
  eine Vertrauensstufe und den Schlüssel, der den Index signiert hat, für Wissens- und Rezept-Packs beider
  Vertrauensstufen und für `verified`-Target-Packs; ein `community`-Target-Pack fragt immer. Ein neuer
  Signaturschlüssel des Index macht es ungültig. Jede Installation darunter landet mit ihrer ID im Audit-Log und
  erscheint als Hinweis in der GUI; ein solches Pack ist in seiner `.lcf-provenance.json` und in `list_packs` mit
  `installed_via: grant:<id>` markiert. Für alle lokalen stdio-Clients gemeinsam werden nur der engste Umfang und
  höchstens 30 Tage angeboten. Eine Installation darunter muss die größere Speicherreserve einer dauerhaften Freigabe
  frei lassen (20 GiB oder 10 %), sonst wird gefragt. Jeder Import nimmt eine Reservierung (die doppelte Pack-Größe)
  im gemeinsamen Reservierungsbuch, die beim Start des Imports unter dessen Sperre erneut geprüft wird.
- **`start_recipe_build`** (lokal schreiben): baut ein installiertes Rezept genau so, wie es festgelegt ist: sein
  Hugging-Face-Modell beim Commit des Rezepts, Target, Format, Board, Quantisierung, Importance-Matrix und
  Kontextlänge. Es geht denselben zweistufigen Weg wie `start_build` (die Frage zeigt das Rezept, ob das
  installierte Target das ist, mit dem das Rezept entstand, seine Importance-Matrix und Kontextlänge, den Commit,
  die Downloadgröße, das Laufwerk, die
  Hardware-Schätzung, Lizenz, Zugangsbeschränkung und Image-Build; dieselbe Speicherreservierung und dieselben
  Kontingente). `probe` nennt die Hardware-Probe des Boards wie bei `start_build` (vor der Frage geprüft; ohne sie
  die neueste gespeicherte Probe eines Boards des SoC des Rezepts). `dataset_path` nur, wenn das Rezept einen
  Kalibrierungsdatensatz nennt, und dann muss es genau diese
  Datei sein (sha256): Der Build kopiert die Datei in seinen eigenen Ordner, während er sie hasht, und benutzt nur
  diese Kopie, sodass eine nach der Frage ausgetauschte Datei nie benutzt wird. Ein nach der Frage in irgendeinem
  Feld geändertes Rezept wird nicht gebaut. „Ja, und nicht mehr fragen“ hat den
  Umfang von `start_build`; ein unter einem Dauerauftrag installiertes Rezept ist erst abgedeckt, nachdem du einen
  Build davon bestätigt hast. Die Ressource `lcf://packs/index` zeigt den zwischengespeicherten Index und die
  installierten Packs (wie `list_packs`, nie aktualisiert).
- **`export_pack`** (lokal schreiben, immer gefragt, nie „nicht mehr fragen“): ein Target- (über die Target-ID),
  Rezept- (über die `artifact_id` eines fertigen Builds) oder Wissens-Pack, mit Datenschutz-Bereinigung und mit
  deinem Community-Schlüssel signiert, nach `community/contributions/`. Die erste Zeile der Frage sagt „Creates a pack
  signed with your key <Schlüssel-ID> as author <Autor>“. Es lehnt ab, solange kein Signaturschlüssel existiert (es
  legt nie einen an; das tust du mit `llm-cli zoo keys`), und für ein Community-Target eines anderen Autors. Der
  Autor ist standardmäßig der deines letzten Exports. Das Ergebnis enthält die Datei als Label, die Schlüssel-ID und
  je Datei, wie viele Zeilen die Bereinigung geändert hat, nie den entfernten Text. Ein Wissens-Export läuft als
  Job. Es nimmt keine Empfänger, keine Passphrase, keinen Ausgabeordner und keine Pull-Request-Option: Veröffentlichen
  bleibt dein Schritt (`llm-cli zoo export --pr` oder der Hub).

### Werkzeuge für Maintainer

Für den Maintainer des Community-Repositorys (`community_repo`), nicht für Benutzer. **Tools > Community Hub öffnen >
Maintainer** und `llm-cli zoo maintainer` ([CLI_Usage](CLI_Usage_DE.md#zoo-maintainer)) erledigen die Arbeit
in drei Aktionen, mit einer Implementierung für beide (`orchestrator/Core/community_maintainer.py`). GitHub wird mit
deinem eigenen Login der GitHub CLI (`gh`) und git erreicht; das Framework liest, speichert und zeigt nie einen Token.
Nichts davon ist für eine KI erreichbar: kein MCP-Tool, kein Weg von Ditto und kein anderes Tool ruft es auf (ein Test
sichert das ab).

1. **Signaturschlüssel anlegen...** (einmalig; `zoo maintainer key NAME`): ein neuer Ed25519-Signaturschlüssel für
   den Index im Schlüsselbund des Betriebssystems (Dienst `lcf-community-index`). Ein Name, den es schon gibt, wird
   abgelehnt: ein Schlüssel wird nie ersetzt. Sein öffentlicher Eintrag für `configs/community_keys.yml` (zwei Jahre
   gültig) wird mit **Kopieren** und **Speichern...** (`--save DATEI`) gezeigt; er erreicht alle mit dem nächsten
   Framework-Release.
2. **Prüfen und veröffentlichen** (`zoo maintainer review [ITEM]`): **Beiträge laden** listet die offenen Pull
   Requests, die Packs unter `contributions/<kind>/` hinzufügen, mit dem Stand der Pflichtprüfung `validate`, und
   Packs, die dort gemergt, aber noch nicht veröffentlicht sind. **Auswahl prüfen** holt die Packs vom Stand des Pull
   Requests in einen lokalen Klon des Repositorys im benutzereigenen Ordner, prüft jedes mit `zoo verify` und zeigt
   jede Datei (`pack.json`, `pack.sig`, jede Datei des Packs und jedes andere Element der Pack-Datei) in einer Liste
   mit einer schreibgeschützten Textansicht (die CLI gibt sie aus oder schreibt sie mit `--review-dir` in einen neuen
   oder leeren Ordner, den du nennst). Von hier werden nur neue Pack-Dateien unter `contributions/<kind>/` gemergt:
   Ein Pull Request, der auch etwas anderes ändert (den Prüf-Workflow, den Index, eine README, eine Löschung) oder in
   einen anderen Branch als `main` geht, wird nicht veröffentlicht, und die Prüfung nennt diese Änderung.
   **Veröffentlichen...** fragt „Veröffentlichen?“ („Nein“ ist die Vorgabe). Bei Ja wird der Beitrag
   gemergt (nur wenn die Prüfung bestanden ist und sein Stand noch der geprüfte Commit ist), der Klon aktualisiert,
   das Pack nach `packs/<kind>/` verschoben, sein Index-Eintrag bekommt die Raw-Adresse
   (`https://raw.githubusercontent.com/<repo>/main/packs/<kind>/<datei>`), das Vertrauen (`community`, oder
   `verified`, wenn jemand anderes als der Autor es nachgebaut hat), den Titel und bei einem Wissens-Pack das Thema
   oder Target seines Manifests (die Prüfzeile zeigt es auch: „belongs to topic ...“), die Version des Index wird
   erhöht und seine Zeit gesetzt, `index.json` mit dem Schlüssel aus dem Schlüsselbund signiert, und diese Änderung
   auf einem neuen Branch (`lcf-publish-...`) committet, gepusht, als Pull Request geöffnet und gemergt, sobald die
   Pflichtprüfung bestanden ist. Nichts wird je nach `main` gepusht (der Branch ist geschützt), und jeder Merge legt
   den geprüften Commit fest.
3. **Veröffentlichtes Pack zurückziehen...** (`zoo maintainer revoke KIND ID --reason ... [--version V | --sha256
   S]`): der Rückzug (genau eine Datei, eine Version oder jede Version der ID, mit dem Grund, den alle sehen) kommt auf
   demselben Weg in den signierten Index. Der Eintrag bleibt, damit der Hub das Pack als zurückgezogen zeigt.

Jeder Schritt wird gezeigt, sobald er geschieht, und ins Audit-Log geschrieben (Tool `zoo_maintainer`; ohne Audit-Log
beginnt nichts). **Abbrechen** (Strg+C im Terminal) hält vor dem nächsten Schritt an, bis zum letzten Merge; nach
dem Push schließt es auch den eigenen Pull Request der Veröffentlichung und löscht seinen Branch, damit nichts halb
Veröffentlichtes zurückbleibt (ein gemergter Beitrag wartet unter `contributions/` und steht wieder in der Liste).
Bevor etwas signiert wird, muss sich der Index auf `main` mit deinem Schlüssel oder einem vertrauenswürdigen Schlüssel dieses
Frameworks prüfen lassen. Eine Ablehnung nennt den Grund, was auf GitHub bis dahin geschehen ist (zum Beispiel ein Pull
Request, der offen bleibt) und den nächsten Schritt: kein `gh` oder `git`, nicht angemeldet (`gh auth login`), offline,
eine nicht bestandene Prüfung, ein Pack, das `zoo verify` ablehnt, eine veröffentlichte Datei gleichen Namens, ein Klon
mit eigenen Änderungen (sie werden nie verworfen).

- Von Hand: `scripts/zoo_sign_index.py`, eine dünne Hülle über denselben Code, legt den Schlüssel an
  (`keygen <Name>`), gibt den Index-Eintrag eines geprüften Packs aus (`entry <pack.zip> --url <https-Adresse>`) und
  signiert einen Index (`sign index.json --key <Name> [--previous <alte index.json>]`; die Version muss höher sein als
  die vorige).
- `scripts/community_repo/validate-contribution.yml` ist die Vorlage für den CI-Workflow des Community-Repositorys.
  Sie läuft bei jedem Pull Request in `main` (sie ist die Pflichtprüfung `validate` des geschützten `main`) und kommt
  immer aus `main` selbst (`pull_request_target`): Ein Pull Request aus einem Fork kann die Prüfung, die ihn prüft,
  nicht umschreiben. Der Job liest nur (`contents: read`, kein Secret, kein Cache, sein einziger Checkout, der des
  Frameworks, behält keine Zugangsdaten). Die Dateien des Pull Requests sind nur Daten und werden nie ausgecheckt:
  Nachdem das Framework installiert ist, werden `main` und der Head-Commit (über seine ID) still und ohne Zugangsdaten
  in ein Repository ohne Arbeitsverzeichnis geholt, also wird keine Datei des Pull Requests je ausgeschrieben, nichts
  davon wird ausgeführt, eingelesen (`source`), installiert oder importiert, und die Schritte lesen es nur mit git
  (ein Checkout würde auch den Betreff des Commits ausgeben, in dem ein Wagenrücklauf einen Workflow-Befehl beginnt);
  nichts, was der Pull Request festlegt (Titel, Branch, Commit-Nachrichten, Dateinamen), wird in ein Skript
  geschrieben. Die Änderungen berechnet git zwischen `main` und dem Head-Commit (ab ihrer einen Merge-Basis, so wie
  GitHub sie zusammenführt: Ein Pull Request ohne oder mit mehr als einer Merge-Basis mit `main` fällt durch, weil der
  Merge von GitHub dann Basen verbindet, die die Prüfung nicht sieht; ein Verschieben zählt als Löschen und
  Hinzufügen). Ein Dateiname mit einem Steuerzeichen, ein symbolischer Link, eine ausführbare Datei oder ein Submodul
  fällt für jeden durch. Ein Pull Request von jemand anderem als dem Besitzer des Repositorys, einem Mitglied oder
  einem Collaborator darf nur Pack-Dateien `contributions/<kind>/<name>.zip` hinzufügen; alles andere, was er
  hinzufügt, ändert oder löscht (den Workflow, `index.json`, `packs/`, eine README, auch die README eines
  Kind-Ordners, eine Löschung), fällt durch, und der Fehler nennt die Datei. Die übrigen Änderungen des Besitzers,
  eines Mitglieds oder eines Collaborators, etwa die Veröffentlichung des Maintainers (das Pack nach `packs/`
  verschoben, der signierte Index geändert), erscheinen als Hinweise und bestehen. Jedes hinzugefügte Pack, egal von
  wem, wird aus den Objekten von git kopiert und muss höchstens 5 MB groß sein und `llm-cli zoo verify` bestehen,
  dessen Datenschutz-Bereinigung auch ein Pack mit einem Schlüssel in einer seiner Dateien abweist (der Secret-Scan des
  Repositorys überspringt Binärdateien und läuft deshalb nicht über die ZIP-Dateien); was es ausgibt, wird nie als
  Workflow-Befehl gelesen. Der Signaturschlüssel kommt nie in die CI. Der Maintainer trägt das Framework-Release ein,
  mit dem die Packs geprüft werden (`LCF_REF`); bis dahin steht in der Vorlage ein Platzhalter, und der Job schlägt
  fehl. Das Repository braucht außerdem eine Einstellung: Ein Pull Request kann immer noch einen eigenen Workflow
  mitbringen, der bei `pull_request` läuft und einen Job namens `validate` hat; unter Settings > Actions > General muss
  "Approval for running fork pull request workflows from contributors" deshalb auf "Require approval for all external
  contributors" stehen (diese Prüfung braucht keine Freigabe, und der Workflow-Lauf eines Forks wird nie
  freigegeben). Diese Fassung in eine Kopie bringen, die noch bei `pull_request` läuft: Der Pull Request, der sie
  ändert, bekommt von keiner der beiden Kopien ein `validate`; ergänze deshalb in genau diesem Pull Request
  `pull_request:` neben `pull_request_target:` und entferne es im direkt folgenden wieder.

### Was entfernt wurde

- Das „Swarm“-Hochladen: die XOR-„SwarmCipher“ (eine Verschleierung, die jeder rückgängig machen konnte),
  `llm-cli swarm upload`, der Knopf „Contribute to Swarm“ im Hub und seine Abfrage eines GitHub-Tokens. Ein altes
  Swarm-Paket in `community/knowledge/` ist eine lose Datei, die nie importiert wird; das Einsortieren wird mit dem
  Grund abgelehnt.
- Das Installieren von Hand kopierter Modulordner aus `community/`. Community-Packs werden mit `llm-cli zoo install`
  oder dem Hub installiert.

---

## Nächste Schritte und die Menüleiste

Das Fenster sagt dir nie, dass du einen Befehl tippen sollst. Eine Meldung sagt, was nicht stimmt; ein eigener
**nächster Schritt** sagt, was zu tun ist, und derselbe Schritt liest sich für jedes Publikum anders:

- **Im Fenster** liest du, wo du klickst, in deiner Sprache, mit einer Schaltfläche **Öffnen**, die dorthin führt:
  „Dittos lokales Modellprogramm bauen: Ditto > Erweitert > Lokale Laufzeit... > llama-server bauen…". Im selben
  Fenster nennt der Schritt nur die Schaltfläche.
- **Auf der Kommandozeile** gibt derselbe Schritt wie bisher den Befehl aus: `Nächster Schritt: Dittos lokales
  Modellprogramm bauen: llmcf ditto runtime install --yes`.
- **Eine KI** bekommt das Tool, das sie aufrufen kann, oder „das kann nur die Person" mit dem Ort im Fenster; eine
  KI, die für das Fenster arbeitet (der Kanal des Aufrufs ist der Chat des Fensters oder das Fenster), bekommt nie
  einen Befehl, eine KI-App an einer Kommandozeile bekommt auch den Befehl. (Der Chat kennzeichnet seine Aufrufe mit
  diesem Kanal in einem späteren Schritt der Welle 6.)

Schritte, die an einen anderen Ort gehören (ein Befehl auf dem Board, ein Befehl mit Administratorrechten, der eigene
Befehl eines anderen Programms), stehen mit diesem Befehl und einer Schaltfläche **Kopieren** da; das Framework führt
sie nie aus.

**Einen Befehl zulassen (Mensch entscheidet):** Braucht Ditto oder Mojo einen Schritt, den nur du zulassen darfst,
bereitet das Framework den genauen Befehl aus seinem eigenen Katalog vor und fragt: „`llmcf ...` zulassen? Was er tut.
Warum er jetzt nötig ist." Ja führt genau diesen Befehl mit dem eigenen Python des Frameworks aus und zeigt seine
Ausgabe; Nein führt nichts aus. Der Befehl ist nie freier Text einer KI: Er ist ein Schritt des Katalogs mit
geprüften Werten (Auswahlen, Namen, IDs; nie ein Pfad, den du nicht gewählt hast). Ein Schritt, der etwas löscht,
öffnet die Liste dessen, was gelöscht wird (Nein ist vorgewählt); ein Passwort oder Schlüssel wird in einem verdeckten
Feld abgefragt und über die Standardeingabe übergeben, nie auf der Kommandozeile. Was läuft, ist genau das, was du
gelesen hast: Ein geänderter Vorschlag wird abgelehnt. Zugelassen werden nur Befehle, die ohne Terminal durchlaufen
und nicht mehr tun, als die Frage sagt; alles andere (zum Beispiel eine Lizenz zu lesen) öffnet seinen Ort im Fenster.
Jede Entscheidung steht im Aktivitätsprotokoll. (Die Frage und ihre Konsole im Fenster kommen mit der Ditto-Konsole.)

**Fehler in deiner Sprache:** Ein Fehler erscheint mit einer verständlichen Zusammenfassung in deiner Sprache, dem
nächsten Schritt und dem englischen technischen Detail zum Aufklappen und Kopieren (Ditto liest das englische Detail
und erklärt es dir auf Deutsch).

**Die Menüleiste** (übersetzt, mit Alt-Tastenkürzeln; die Objektnamen der Einträge bleiben gleich, so behält jeder
Pfad der Kommandozeile seinen Mausweg):

- **Datei:** Papierkorb..., Ausgabeordner..., Einstellungen…, Beenden.
- **Build:** Targets…, Build-Verlauf….
- **Boards:** Boards und Geräte....
- **Ditto:** Ditto einrichten... (zuerst; öffnet die KI-Einstellungen, bis die Einrichtungshilfe kommt),
  KI-Einstellungen..., Tool-Berechtigungen..., Ordner, die die KI nutzen darf..., Offene Freigaben...,
  Korrekturvorschläge von KI-Apps... und **Erweitert**: Lokale Laufzeit..., Lokale Modelle..., Bewertung....
- **Sicherheit:** OpenAI-API-Key festlegen..., Geheimnisse..., Anmeldeübersicht..., Schlüsselspeicher...,
  Prüfprotokoll..., Verbindungen zu KI-Apps (MCP)....
- **Ansicht:** der Chat-Bereich und das Schreiben im Chat.
- **Extras:** Hardware-Modul erstellen..., Hugging-Face-Modellbrowser..., **Wissen** (Community Hub öffnen,
  Wissens-Snapshots..., Wissensquellen..., Dokumentation des Frameworks indexieren..., Hinweis zu Deep Ingest...),
  **Wartung** (Build-Images des Frameworks..., Mojo-Gesundheitscheck...), Gesprochene Ansagen, Eingabeverlauf
  löschen....
- **Hilfe:** Über....

Ein Text, der einen Ort nennt, schreibt `{path:<objectName>}`; das Fenster füllt ihn aus den Beschriftungen auf dem
Bildschirm in deiner Sprache, so passt ein Pfad in einer Meldung immer zum Menü, das du siehst. Für Mitwirkende:
[CONTRIBUTING_DE.md](../CONTRIBUTING_DE.md#nächste-schritte-statt-befehlen).

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

- **Das Framework im Container baut keine Modelle:** Das Image, das du selbst baust
  ([Container-Einrichtung](Container_Setup_DE.md)), enthält kein Docker; Konvertierungen laufen deshalb auf einem
  Computer, auf dem das Framework direkt installiert ist.
- **Windows-Hosts:** Konvertierungen auf einem Windows-Host sind nicht geprüft.
- **Anmeldung im Browser und bei Cloud-Plattformen:** OAuth-Abläufe und die Anmeldeketten von AWS, Google Cloud
  und Azure gibt es noch nicht; Ditto meldet sich mit API-Schlüsseln an, und lokale Engines brauchen keine
  Anmeldung.
- **Laufzeit auf dem Gerät:** Die selbst gebaute llama.cpp-Laufzeit auf einem Board funktioniert über die
  Kommandozeile und ist mit einem nachgebildeten SSH-Kanal getestet, noch nicht auf einem echten Board; eine GUI oder
  ein MCP-Werkzeug dafür gibt es noch nicht.
- **GUI:** Die Reiter Auto-Tuning und Models sind Platzhalter (die lokale Laufzeit steht in **Ditto > Lokale
  Laufzeit...**).
- **Deep Ingest** läuft nur aus dem Modul-Assistenten in der GUI; einen CLI-Befehl dafür gibt es nicht. Einen
  Schnappschuss der Wissensbasis wiederherstellen kann man über GUI oder CLI nicht.
- **Reranking** läuft nur bei Build-Empfehlungen; `rag_rerank_default` nimmt den Chat und das MCP-Tool
  `search_knowledge` hinzu.
- **Gesprochene Ansagen** sind nur mit nachgebildeten Sprachausgaben getestet, nicht mit den echten Stimmen von
  Windows, macOS oder Linux.
- **Community-Zoo:** Das Community-Repository ist festgelegt (`community_repo`: `Smilez1985/Edge-LLM-Knowledge-Base`),
  sein Beitrags-Workflow, sein signierter Index und der Maintainer-Schlüssel sind aber noch nicht veröffentlicht
  (`configs/community_keys.yml` ist leer, `community_index_url` bleibt leer, bis dort der erste signierte Index
  liegt); installieren lassen sich deshalb nur Pack-Dateien.
- Kleinere Lücken der CLI stehen in [CLI_Usage_DE.md](CLI_Usage_DE.md#bekannte-einschränkungen).

Was geplant ist: [Roadmap](ROADMAP_DE.md). Was sich geändert hat: [CHANGELOG](../CHANGELOG_DE.md).
