Version 3.2.0 · Stand 2026-10-29

[English](ROADMAP.md) · **Deutsch**

# Roadmap

Was als Nächstes kommt. Versionsnummern und Inhalte können sich noch ändern; nichts in diesem Dokument ist Teil
einer veröffentlichten Version. Es ist eine der drei Dateien des [Bauplans](BLUEPRINT_DE.md): Wie das Framework
gebaut ist und warum, steht im [Design](DESIGN_DE.md), was jede Version enthält, im [Changelog](../CHANGELOG_DE.md).
Die neueste Version ist 3.1.0 (siehe Changelog).

Status je Punkt:

- **in Arbeit**: wird gerade gebaut.
- **geplant**: beschlossen, noch nicht begonnen.

Ist ein Punkt fertig, verlässt seine Zeile die Roadmap im selben Commit, der seinen Eintrag in den Changelog schreibt,
unter der Version, in der er landet; ein teilweise fertiger Punkt behält nur, was fehlt. Jede Zeile nennt die Runde
der Entscheidungen des Maintainers, aus der sie stammt.

| Version | Thema | Status |
|---|---|---|
| 3.2.x | Dittos Kontext, das Board-Bündel, die Laufzeit auf dem Gerät und mehr Hardware | in Arbeit |
| als Nächstes | Build-Dienst, Warteschlange und Modi | geplant |
| 3.3.0 | LoRA und große Modelle | geplant |
| 3.4.0 | Lieferkette und Community | geplant |
| später | Rust-Teile für das Netzwerk und das Board | geplant |
| später | Mehr Hardware und MCP-Erweiterungen | geplant |

## 3.2.x - Dittos Kontext, das Board-Bündel, die Laufzeit auf dem Gerät und mehr Hardware (in Arbeit)

Diese Version kommt in dieser Reihenfolge: zuerst Dittos Kontext, daneben das Board-Bündel, dann das Gateway,
Sprache und MCP auf dem Board. Die erste Hardware setzt die Prioritäten für die Laufzeit auf dem Gerät:
RK3566-Boards, NVIDIA-RTX-GPUs und der AMD Ryzen Z1 Extreme, unter Windows und Linux. Die Rezepte kommen dort
zuerst; die Rotationsstufe und das allgemeine ARM64-Target folgen direkt danach.

### Aus 3.1.0 übernommen

| Punkt | Status |
|---|---|
| **Anmeldung je Zugangsart** für alle gängigen Cloud- und API-Anbieter und für lokale Engines (Einzelheiten unten). | in Arbeit |
| **Dokumentation auf Englisch und Deutsch, der Rest**: Jedes Dokument hat seinen deutschen Zwilling (geprüft von `tests/test_docs.py`); offen ist noch der Durchgang über die Texte jedes Fensters (siehe „Jedes Fenster übersetzt“ unter Später). | in Arbeit |
| **Community-Repository und Index veröffentlicht**: Das Repository ist gewählt ([Smilez1985/Edge-LLM-Knowledge-Base](https://github.com/Smilez1985/Edge-LLM-Knowledge-Base), seit 2026-10-22 der Standardwert von `community_repo`, Pull Requests gehen also schon dorthin). Noch zu tun: sein Beitrags-Workflow, der erste signierte Index, der Schlüssel des Maintainers in `configs/community_keys.yml` und die ausgefüllte Einstellung `community_index_url`, ausgeliefert mit einem Release. Bis dahin installiert der Zoo nur Paketdateien. | geplant |

Anmeldung je Zugangsart, wie entworfen. Schon gebaut (im Changelog aufgeführt): der API-Schlüssel zuerst bei
Anthropic, die zweite Fassung des Secrets-Speichers, die eine Mustertabelle, der Kern der Anmeldung (Verfahren je
Anbieter, Prüfung, Fehler mit nächstem Schritt) und der Anmeldebereich der GUI mit den passenden CLI-Befehlen
(`llm-cli ditto login`, `logout`, `auth`) für API-Schlüssel, lokale Engines und das `ant`-Profil.

- Ein Anmeldebereich je Zugangsart in der GUI und passende CLI-Befehle.
- **API-Schlüssel zuerst.** Bei Anthropic gilt der API-Schlüssel, es sei denn, du wählst selbst ein Profil von
  `ant auth login`; nichts wechselt stillschweigend die Zugangsdaten oder das abgerechnete Konto. OpenAI, Google
  Gemini, OpenRouter und weitere OpenAI-kompatible API-Dienste bekommen Vorlagen mit Schlüsselseiten und Prüfungen.
- **Anmeldung im Browser nur, wo der Anbieter sie für Drittanbieter-Apps erlaubt**: OpenRouter (OAuth mit PKCE,
  liefert einen API-Schlüssel), Hugging Face mit einer eigenen registrierten App des Frameworks und „Sign in with
  ChatGPT“ für OpenAI-Tarife (Beta, bis es mit einem echten Konto getestet ist). Das Framework nutzt nie die
  Anmeldung eines anderen Programms mit.
- **Cloud-Plattformen** (AWS, Google Cloud, Azure) über die eigenen Anmeldeketten der Anbieter, als optionale
  Erweiterungen; gespeichert werden nur Auswahlwerte wie Profil und Region.
- **Lokale Engines nur als Client**: Ollama, LM Studio, llama.cpp, vLLM, Jan, Lemonade, Foundry Local und Unsloth
  Studio. Engines werden an ihren Antworten erkannt, nicht an ihrem Port; Schlüssel sind an die Adresse der Engine
  gebunden; eine Warnung erscheint, wenn eine Engine ohne Schlüssel aus dem Netz erreichbar ist; Ditto schaltet nie
  serverseitige Tools ein.
- **Secrets-Speicher, zweite Fassung**: verschlüsselter Tresor mit einer Sperre über Prozessgrenzen und
  Schlüsselwechsel; ist kein Schlüsselbund nutzbar, wählst du einen Ausweichweg ausdrücklich.
- **Eine Mustertabelle** für den Secret-Scan des Repositorys und die Schwärzung in Logs.

### Dittos Kontext (DECISIONS Runden 29, 32 und 33)

| Punkt | Status |
|---|---|
| **Erst echte Token-Zahlen.** Die Tokens jeder Anfrage je Anbieter und Modell und die Cache-Treffer zählen, und die 12-Stunden-Lebensdauer des Caches und die Token-Zählung von OpenAI mit einem bezahlten Testaufruf klären, den die Person in ihrem eigenen llmcf mit ihrem eigenen Schlüssel startet (nie aus einer Sandbox oder der CI; der Schlüssel verlässt den Rechner nie). Runde 29 (Q1), Runde 33 (Punkt 2). | geplant |
| **Kleinere Tool-Ergebnisse und sauberere Logs** für jedes Modell: Was ein Tool zurückgibt und was eine Log-Zeile trägt, schrumpft auf das, was das Modell braucht. Runde 29 (Q1). | geplant |
| **Ein Speicherbudget für Dittos lokalen Server**: der KV-Cache nach derselben automatischen Regel quantisiert wie auf Boards (`local_kv_cache = auto`), das RAM-Budget aus der gemessenen Hardware, ein Abstand für das Betriebssystem. Runde 29 (Q1, Q4), Runde 32 (Kontext 4). | geplant |
| **Das echte Kontextfenster je Modell**, mit Zählen, Budget und Verdichtung: Dittos lokales Modell aus den gemessenen Grenzen der Hardware, API-Modelle je Modell, beim Server des Anbieters erfragt, wo er es sagt. Runde 29 (Q3), Runde 32 (Kontext 4). | geplant |
| **Cloud-Caching so, wie der Anbieter es vorsieht**: das eigene Caching des Anbieters und seine Anleitungen für Cache-Punkte (Anthropic, OpenRouter), die Lebensdauer des Caches eine Einstellung mit kurzer Vorgabe und einer Kostengrenze als weicher Obergrenze. Runde 29 (Q4), Runde 32 (Kontext 5). | geplant |
| **Dittos Archiv, Phase 1: Fakten aus Tool-Ergebnissen.** Ein durchsuchbares Archiv dessen, was Ditto über llmcf und das Bauen gelernt hat (nie die privaten Gespräche der Person), lesbar für alle, die den Harness nutzen, in einer eigenen Collection des vorhandenen Speichers der Wissensbasis; ein neuerer Fakt ersetzt den älteren. Runde 29 (Q2), Runde 32 (Kontext 2, 3), Runde 33 (Punkt 1). | geplant |
| **LLMLingua-2 bei Bedarf**, standardmäßig aus: etwa 150 Zeilen der MIT-Methode übernommen (ein numpy-Runner in der App), das Modell bei Bedarf von seiner Originalquelle geholt und nur, wenn es fehlt, nie im Repository mitgeliefert. Runde 29 (Q5), Runde 32 (Kontext 1). | geplant |
| **Dittos Archiv, Phase 2**: Fakten aus Dittos eigenen Antworten, Export und Import, Schnappschüsse. Runde 33 (Punkt 1). | geplant |
| **Gespeicherte Gesprächs-Caches auf der Platte**, nur wenn die Messung zeigt, dass Leute oft zurückkommen, nachdem der Server geschlafen hat. Runde 29 (Q1). | geplant |

### Board-Bündel, Gateway, Sprache, MCP auf dem Board (DECISIONS Runden 31 bis 35)

| Punkt | Status |
|---|---|
| **Das Board messen, bevor eine Board-Vorgabe gesetzt wird**: Die RAM-Reserve eines Boards kommt aus einer Messung auf diesem Board, nie aus einer Schätzung. Runde 32 (Board 3). | geplant |
| **Das Startskript gibt Kontext, Cache-Budget und eine Reserve je Board weiter.** Runde 32 (Board 3, 5). | geplant |
| **Fertig gebaute, signierte Rust-Programme für Boards** (die Board-Seite von „Die Rust-Teile des Frameworks fertig gebaut und signiert ausgeliefert“ unter Rust-Teile). Runde 32 (Board 1, Q6). | geplant |
| **Gemeinsame goldene Vektoren für die Kontextregeln**, damit der Rust-Code des Boards und der Python-Code des Hosts gleich entscheiden. Runde 32 (Q6). | geplant |
| **Das Bündel, Deploy-Weg 3**: ein vollständiges Bündel mit Hash-Liste, das die Person auf dem Host vorbereitet (mehrere Bestandteile, jeder in einem eigenen Schritt cross-kompiliert), mit signiertem Manifest und eigenem Speicher; es steht neben Weg 1 (goldenes Artefakt und `deploy.sh`) und Weg 2 (Laufzeitpaket) und ruft ihren Code auf, statt ihn zu kopieren. Runde 33 (Punkt 3), Runde 35. | geplant |
| **Ein idempotenter Installer auf dem Board**: Benutzer, Units, cgroups, Dateien, Hashes, Rücknahme und Deinstallation, über den Rust-Root-Helfer `llmcf-install`; root nur über `sudo -n` mit dem Ja der Person für dieses Deployment, oder Benutzer, die die Person vorher angelegt hat; offline ist das Bündel vollständig, online holt der Installer nur festgelegte, per Hash geprüfte Teile des Manifests. Runde 33 (Punkt 3 und Nachtrag), Runde 32 (Board 6), Runde 35. | geplant |
| **`llmcf-gateway`, Stufe 1 in Rust**: eine Token sparende Zwischenschicht vor dem lokalen Modell auf dem Board; sie startet und stoppt `llama-server`, der in einer eigenen Unit außerhalb der Speichergrenze des Gateways läuft. Runde 29 (Q6), Runde 32 (Board 1, 8). | geplant |
| **Das Gateway als Bestandteil des Bündels**, mit Prüfung und Benchmark; zuerst nur Loopback, das lokale Netz nur mit TLS (eigenes Zertifikat, festgelegter Fingerabdruck), nie einfaches HTTP. Runde 32 (Board 2). | geplant |
| **Sprache mit whisper.cpp** für Spracherkennung und Weckwort; andere Sprachteile (NeMo, das GigaSpeech-Weckwort, eSpeak NG, ein fertiges onnxruntime) nur als Opt-in mit ihrer Lizenz vor dem Download gezeigt, nie standardmäßig an bei unfreien Bedingungen. Runde 32 (Sprache, Board 4). | geplant |
| **Bilderkennung mit `--mmproj`** als Bestandteil des Bündels. Runde 33 (Punkt 3). | geplant |
| **Ein Agenten-Harness als Bestandteil des Bündels**, auf einem festgelegten Commit, unter einem eigenen Linux-Benutzer, installiert von seinem eigenen Installer nach dem Ja der Person; llmcf blockiert nicht, was dieser Installer holt, beobachtet, was er tut (Netzziele, Pakete, Dateien, Prozesse), markiert Verdächtiges und berichtet vorher und nachher. Runde 33 (Punkt 3), Runde 35. | geplant |
| **Ein Fenster „Board-Bündel...“, CLI und MCP**: Die Person wählt die Bestandteile, mit Vorlagen und einem Vorschlag von Ditto; ob das LLM auf dem Board läuft, entscheidet zuerst die Person, dann die gemessenen Grenzen; das MCP-Tool `bundle_deploy` steht neben `deploy_device_runtime`. Runde 32 (Board 5), Runde 35. | geplant |
| **Ein Vertragstest für Harnesses**, die sich an das Gateway anschließen, ohne Harness-Code in llmcf. Runde 32 (Board 7). | geplant |
| **Ein optionaler Sitzungsspeicher auf dem Board**, jederzeit, nachdem das Gateway deployt ist. Runde 29 (Q6). | geplant |
| **Ein MCP-Server im Gateway**: nur Loopback, zuerst lesende Tools (Modellstatus, Zustand, Benchmark, Logs). Runde 31, Runde 32. | geplant |
| **Der Host erreicht das MCP des Boards über SSH-stdio** (Tools `device_board_*` neben `probe_remote_device` und `deploy_device_runtime`); er spricht mit dem eigenen Gateway des Boards, nie mit einer anderen Installation von llmcf. Runde 31. | geplant |
| **Freigaben für die Tools des Boards**, über die vorhandenen ausstehenden Freigaben: Ein Harness darf seine eigene Person fragen, außer einem Harness, der in den Raum hört; dauerhafte Freigaben nur als Empfehlung mit weicher Obergrenze angeboten. Runde 32 (Board 7). | geplant |
| **Modell, Sprache und Bilderkennung auf dem Board wechseln, neu starten, messen** über MCP. Runde 31. | geplant |
| **Brückenmodus mit Autodiscovery**: Das Gateway verbindet sich mit MCP-Servern, die schon laufen, und bietet ihre Tools an einer Stelle an; gefundene Tools werden aufgelistet, aber erst angeboten, wenn die Person sie ankreuzt, überbrückte Tools gelten als nicht vertrauenswürdig, und das Gateway startet nie einen Server. Runde 31, Runde 32 (Board 6, Brücke). | geplant |
| **Die eigene Tool-Schleife des Gateways** für das lokale Modell des Boards, mit Tool-Profilen. Runde 31. | geplant |
| **MCP auf der Netzadresse des Boards**, nur mit TLS und dem festgelegten Fingerabdruck. Runde 32 (Board 2). | geplant |

### Beschlossen, wartet auf seine Reihe

| Punkt | Status |
|---|---|
| **Nacharbeiten am Mojo-Kreis**: Die Notiz eines offenen Oracle-Laufs verfällt nach einer Stunde; `llmcf mojo --miracle --json` und `llm-cli doctor --fix` löschen sie ohne Logo, ebenso ein Lauf von Mojo Miracle in der GUI; die Funktionen der Notiz werfen bei keinem kaputten Zustandsordner. Runde 30. | geplant |
| **Ditto: Sicherheit und Messbarkeit**: Eval-Urteile mit Fingerabdruck, Wiederholungen mit Seed, Intervalle, pass^k und eine Sperre bei Rückschritt, eine Regel für Runden, die Text von außen befleckt hat, die Quelle an jedem Wissensauszug, neue Gruppen von Eval-Fällen, Wiederholungsfälle und die Bewertung des ersten Tool-Aufrufs, vollständigere Zahlen zur Tool-Nutzung, das Packen von Auszügen für kleine Kontexte und Zusammenfassungen, die die Fakten behalten. Runde 13 (P2). | geplant |
| **Offline-Builds des Build-Container-Images**: Der Offline-Modus darf das Image mit der Werkzeugkette bauen, wenn jede Quelle nach der einen Lokalitätsregel lokal ist (Basis-Images auf diesem Rechner oder in einer Registry im eigenen Netz, llama.cpp vom lokalen Spiegel oder aus einer lokalen Kopie, apt und pip nur über Spiegel oder Caches im eigenen Netz, die die Person einstellt), sonst hält er früh an und nennt die fehlende Quelle; dazu Import und Export eines Build-Container-Images als geprüfte Datei für Rechner ohne Netz. Runde 36. | geplant |
| **Vollmachten**: Eine Freigabe, die die Person im Framework erstellt (nie über MCP), lässt eine KI ihre eigenen Routinefragen beantworten, Hinweise zur Lage eingeschlossen; Sicherheits- und Rechtshinweise, Lizenzen, das erste Vertrauen in einen Host-Schlüssel, die eigenen Rechte der KI und jedes Löschen bleiben bei der Person; Laufzeiten 8 Stunden, 24 Stunden oder 30 Tage, „bis zum Widerruf“ verfällt nach 90 Tagen ohne Nutzung. Runde 5 (a, b, f), Runde 6 (Punkt 1). | geplant |
| **„Für diese Modellversion nicht mehr fragen“** als Kästchen in der Build-Frage, auf den festen Codes der Hinweise. Runde 5 (r, Q3). | geplant |
| **Ein Postfach und ein Weckruf für jeden MCP-Client**: ein Hinweis bei jedem Ergebnis plus Tools zum Lesen und Antworten; ein optionaler Weckruf, den die Person einrichtet, trägt nur feste Stichwörter („Build fertig“, „Fehler beim Bauen“), die Einzelheiten kommen über MCP; der Broker ist der MQTT-Broker des Rust-Gateways (Rust-Teile). Runde 5 (o), Runde 6 (Punkte 4-7), Runde 7 (Punkt 8). | geplant |
| **Ein eigenes Konto `llmcf` auf dem Host und auf Boards**: Der Installer legt es an und gibt ihm die Ordner, die es braucht (unter Windows unter dem Programmordner mit ausdrücklichen Rechten), damit ein Pfad, der eine KI erreicht, immer gleich ist; auf einem Board gibt die Person das Admin-Passwort einmal ein, das Framework legt das Konto und seinen Schlüssel an und behält den Schlüssel, nie das Passwort. Runde 5 (q), Runde 6 (Punkte 2, 3), Runde 7 (Punkt 1). | geplant |
| **Anwesenheitsnachweis und Zugriffskontrolle als Opt-in**: Ein Klick reicht zum Bestätigen; PIN, Passwort oder Fingerabdruck nur, wenn die Person sie einschaltet; Benutzerkonten im Framework nur als Opt-in. Runde 7 (Punkte 3, 5, 6). | geplant |
| **Eine eigene Target-Vorlage für den RK3566**: das LLM auf der CPU (Cortex-A55, NEON, GGUF), RKNN nur als optionaler Schritt für Sprach- und Bildmodelle, der den LLM-Build nie blockiert; die anderen Rockchip-SoCs behalten die Standardvorlage, auf die neuesten Toolkits gebracht. Runde 17 (K1-K3). | geplant |
| **Der Update-Orchestrator nativ übernommen**: Ein Update-Orchestrator desselben Autors wird mit festgehaltener Herkunft in llmcf übernommen und dort überarbeitet (sein eigenes Repository bleibt, wie es ist); auf Boards root nur über cron und genaue sudo-Zeilen; OS-Updates ohne Sicherung warten, bis Sicherungen an sind (das Höchstalter der Sicherung eine Einstellung, Vorgabe 14 Tage) oder die Person die Ausnahme für dieses Board ankreuzt; Windows folgt denselben Schritten ohne OS-Updates; llmcfs eigene Releases mit SHA256SUMS und Signatur, der Update-Weg über git bleibt für Entwickler. Runden 18, 19 und 22 (P2, P3). | geplant |
| **Board-Sicherungen holt der PC, zwei Arten von Images**: Der PC holt jede Sicherung über die SSH-Sitzung des Deployments, nichts wird auf die eigene Karte des Boards geschrieben; das Image wird auf dem PC gebaut und verkleinert (ein Assembler ohne Rechte; PiShrink, festgelegt, nur für rohe Karten-Images von außerhalb), das unverkleinerte Image gelöscht, sobald das kleine geprüft ist; eine Gerätesicherung stellt genau dieses Board wieder her, ein Flotten-Image ist verallgemeinert (neue Host-Schlüssel und Machine-ID beim ersten Start, Werte je Board als Variablen aus einer Liste im Framework oder einer Datei auf der Boot-Partition). Runden 20, 21 und 22 (P1, P4). | geplant |
| **Keine harte Zeitgrenze für einen Build-Schritt**: eine weiche Obergrenze, die die Person aufheben kann, ein Schutz vor Schleifen (ein Modell, das sich wiederholt, eine Stufe, die immer neu startet) und eine Grenze für Stillstand statt des heutigen festen `build_timeout`. Runde 25 (Q8). | geplant |

### Laufzeit auf dem Gerät und mehr Hardware

| Punkt | Status |
|---|---|
| **Rezepte als Target-Vorlagen.** Eine Target-Vorlage enthält den Build-Code; Modell, Quantisierung, Format und Deployment sind Variablen, die du im Build-Menü wählst, und der Builder nutzt alles, was ein Rezept enthält. Warum: Du bist nie an die Wahl der Person gebunden, die die Vorlage gemacht hat. | geplant |
| **Rotationsstufe vor der Quantisierung** (SpinQuant, QuaRot), direkt nach den Rezepten: Rotationen werden vor Konvertierung und Quantisierung in die Gewichte eingerechnet, nur wo das Zielformat sie unterstützt (eine Fähigkeit des Formats, kein fester Schritt). Warum: 4-Bit-Modelle der Llama-Familie behalten auf kleinen Boards ihre Qualität. Der Platz ist da: Der Stage-Runner führt das `modules/rotate_module.sh` eines Targets zwischen den Stufen convert und target aus; der Algorithmus kommt noch. | geplant |
| **Allgemeines ARM64-CPU-Target (GGUF)** mit Board-Vorgaben für den Raspberry Pi 4 und 5, direkt nach den Rezepten. Warum: Rockchip war nur der Anfang, und diese Boards brauchen kein Hersteller-SDK. | geplant |
| **Targets aus der Hardware-Probe, auf dem Board bewiesen.** Ein neues Target beginnt mit der Probe, die du auf dem Board ausführst (auch ohne Netz, die Datei zurückgetragen), oder, wenn du willst, mit Ditto, der es über SSH prüft; ein bestandener Benchmark auf dem Board ist der Beweis, gezeigt als „getestet auf <Board>“ im Community-Hub und in Paketen mitgegeben. Warum: Der Maintainer besitzt die meisten Boards nicht, und nur ein Lauf auf dem echten Board zeigt, dass ein Target funktioniert. | geplant |
| **Laufzeit auf dem Gerät, der Rest.** Der erste Lauf der selbst gebauten Board-Laufzeit auf einem echten RK3566-Board; die Geräte-Laufzeit in Deployment-Paketen; das Laufzeit-Image für Docker-Deployments, lokal aus einem Geräte-Build gebaut (statt des geholten Laufzeit-Images); Benchmarks auf dem Gerät und eine gemessene Quantisierungsempfehlung je Board; ein Build auf dem Board, wenn keine Builder-Basis alt genug für seine C-Bibliothek ist; Vulkan auf Mali erst, wenn llama-bench zeigt, dass es den CPU-Build schlägt; MCP-Tools zum Proben, Deployen und Messen von Geräten. | geplant |
| **Selbst gebautes llama.cpp, Stufe 2.** CUDA-Builds (die Redist-Archive von NVIDIA, das Toolkit nach Treiber, Compute Capability, Host-Compiler und CMake gewählt), Windows-Builds (Clang für das CPU-Backend, MSVC nur als CUDA-Host-Compiler; bis dahin Dittos lokales Modell unter Windows und macOS nur über den Opt-in `--prebuilt`), macOS (ungetestet), Benchmarks auf dem PC, die Dittos Modellstufe wählen, und MCP-Tools für die Laufzeit. | geplant |
| **Weitere Rockchip-SoCs für RKLLM** (RK3562, RV1126B) mit einer Tabelle der unterstützten Typen je SoC. | geplant |
| **NVIDIA-GPU-Target**: GGUF mit CUDA für RTX-GPUs der Serien 20 und 30 (CUDA-Version je nach Treiber), die vLLM-Formate AWQ und GPTQ, EXL3 und natives Deployment auf Windows-Rechner mit NVIDIA-GPU. | geplant |
| **GPU-Beschleunigung** für die Importance-Matrix und den RKLLM-Export. | geplant |
| **Target für AMD-RDNA3-APUs** für den Ryzen Z1 Extreme: llama.cpp mit Vulkan und HIP, Linux-Container und natives Deployment unter Windows. | geplant |
| **Prüfung vor dem Build** (`orchestrator/Core/consistency_manager.py`, in Entwicklung): vergleicht die Treiber, die die Hardware-Probe gefunden hat, mit dem, was das SDK des Targets braucht (etwa die CUDA- und Treiber-Matrix), und hält einen Build an, der nicht gelingen kann, bevor er startet; muss noch in den Build-Start eingebunden werden. | geplant |
| **Benchmark-Fenster** (`orchestrator/gui/benchmark_window.py`, in Entwicklung): llama-bench ausführen und die Ergebnisse als Tabelle zeigen; muss noch auf Basis der Laufzeit auf dem Gerät fertig gebaut und in der GUI erreichbar gemacht werden. | geplant |
| **Eine SSH-Verbindung für die Befehle eines Board-Schritts** (OpenSSH `ControlMaster`, als Option): Der OpenSSH-Rückfallweg meldet sich einmal je Schritt an statt einmal je Befehl; geteilte Verbindungen bleiben bis dahin aus, und die eigenen SSH-Einstellungen des Frameworks lehnen sie ab. | geplant |

## Als Nächstes - Build-Dienst, Warteschlange und Modi (geplant; beginnt direkt nach der Architektur-Konsolidierung)

Die Schritte kommen in dieser Reihenfolge. Entscheidungen, die noch offen sind, stehen in ihrer Zeile; sie werden
gefragt, wenn der Schritt beginnt.

| Punkt | Status |
|---|---|
| **Schritt 1 - Build-Dienst**: Ein Hintergrundprogramm je Benutzerkonto führt jeden Build; die GUI, die Kommandozeile und der MCP-Server werden seine Clients. Ein Absturz der GUI beendet keinen Build mehr. Der Dienst lässt sich anhalten und neu starten, ohne dass etwas verloren geht (der Zustand liegt schon in Dateien: Build-Einträge, Build-Anfragen, das Reservierungsbuch, Job-Zusammenfassungen), und ein Build macht bei seiner letzten fertigen Stufe des Vier-Module-Builds weiter (source, config, convert, target). Warum: Heute läuft ein Build in dem Programm, das ihn gestartet hat, und endet, wenn dieses Programm endet. | geplant |
| **Schritt 2 - Eine Warteschlange für alle Programme**: Builds warten in einer Warteschlange im Build-Dienst und werden nach RAM, CPU und Speicherplatz zugelassen. Der Teil für den Speicherplatz existiert (das Reservierungsbuch in `orchestrator/Core/disk_plan.py`); die Warteschlange erweitert, was da ist (die Warteschlange in `orchestrator/Core/orchestrator.py`, die Slots der Build-Engine, den Request Store), statt einer zweiten Struktur. Kein Celery. Warum: Mehrere Programme (GUI, CLI, KI-Apps) starten Builds auf einem Computer. | geplant |
| **Schritt 3 - Privat- und Enterprise-Modus**: Der Privatmodus ist die Vorgabe, der Enterprise-Modus ist optional. Den Modus wählst du im Installer (`install.sh` und der Windows-Installer) und änderst ihn unter Datei > Einstellungen…; eine KI kann ihn lesen, aber nie ändern. Er nutzt das Gerüst für den erweiterten und den Enterprise-Modus, das es in `orchestrator/Core/config_manager.py` schon gibt (Entscheidung U7). Warum: Schritt 4 und 5 bringen Funktionen für Firmen, die eine private Installation standardmäßig nicht braucht. | geplant |
| **Schritt 4 - Secret-Backends**: HashiCorp Vault und CyberArk Conjur (Enterprise-Modus; im Privatmodus als Opt-in). Werte werden abgerufen, wenn sie gebraucht werden, nur im Speicher gehalten und nie in den lokalen Tresor (`secrets.store`) geschrieben; jeder Abruf wird protokolliert, und Leases werden erneuert und widerrufen. Später: dynamische Secrets und signierte SSH-Zertifikate für Boards. Warum: Firmen halten Secrets in einem zentralen Speicher und müssen jede Nutzung sehen. Offene Entscheidungen, gefragt, wenn dieser Schritt beginnt: Audit-Zeilen im CI-Modus; ein Abruf je Vorgang; Referenzen nur im Enterprise-Modus; Backends im Internet; wo die eigene Anmeldung des Backends aufbewahrt wird. | geplant |
| **Schritt 5 - Kubernetes und Nomad**: Orte für Builds als Opt-in, neben den lokalen Docker-Builds, die die Vorgabe bleiben. Sie nutzen Images, die die Firma selbst aus den Dockerfiles des Frameworks in ihre eigene Registry baut, per Digest festgelegt, nie Images aus dem Community Hub. Warum: Eine Firma will Builds vielleicht auf ihrem eigenen Cluster. Offene Entscheidungen: beide Modi oder nur der Enterprise-Modus; über `kubectl`/`nomad` oder ihre HTTP-APIs. | geplant |
| **Schritt 6 - Eigener Schlüsselbund-Dienst unter Linux**: Das Framework bringt unter Linux einen eigenen Schlüsselbund-Dienst mit (beschlossen in Runde 7 am 2026-10-15, nicht gebaut; Windows behält die Windows-Anmeldeinformationsverwaltung); nach Schritt 4. Warum: Viele Linux-Installationen haben keinen nutzbaren Schlüsselbund, und der Tresor lehnt einen ab, der hängt, nichts behält oder im Klartext speichert. Offene Entscheidung: bauen oder beim Schlüsselbund des Systems bleiben. | geplant |
| **Schritt 7 - Neueste passende Versionen, automatisch gehalten**: Jede Abhängigkeit (Python, CUDA, Docker-Basis-Images, Toolkits, GitHub Actions) bleibt auf der höchsten Version, die passt, und jedes Update wird getestet, bevor es übernommen wird. Ein Plan wird vorbereitet. Warum: Alte Versionen sammeln Sicherheitsmeldungen, und ein Update darf nichts kaputt machen. | geplant |

## 3.3.0 - LoRA und große Modelle (geplant)

| Punkt | Status |
|---|---|
| **LoRA-Adapter**: in das Modell einrechnen oder als Adapter behalten (oder beides), mit eigenem Streaming-Merger, GGUF-Adaptern für `llama-server --lora`, RKLLM-LoRA und einem Block `lora` in den Target-Capabilities. | geplant |
| **Optionales LoRA-Training** mit einem eigenen, schlanken Trainer: Ideen von Unsloth, aber kein Unsloth-Code und keine Unsloth-Installation. | geplant |
| **Große Modelle**: eine Prüfung von Speicherplatz und RAM vor dem Build, Wiederverwendung von F16-Zwischenständen, GGUF als Eingabe (Durchreichen und Neu-Quantisieren), Quantisierungsrezepte je Tensor, per KL-Divergenz geprüft, eine Importance-Matrix berechnet, von Hugging Face übernommen oder aus einer Datei geladen, und Prüfungen von Tokenizer und Chat-Vorlage nach jedem Build. | geplant |
| **Vorgaben für Importance-Matrix und Kalibrierung** (Einstellungen `default_enable_imatrix` und `default_calibration_dataset`, in Entwicklung): als Vorgaben in Builder, CLI und den KI-Tools genutzt. | geplant |

Nicht geplant: airllm. Es ist eine Laufzeit, kein Konverter, und gehört nicht in die Pipeline.

## 3.4.0 - Lieferkette und Community (geplant)

| Punkt | Status |
|---|---|
| **Trivy** per Digest festgelegt (es prüft schon ein gespeichertes Archiv, ohne Docker-Socket). | geplant |
| **Festlegungen in den Images**: Basis-Images per Digest, Lock-Dateien mit Hashes je venv. | geplant |
| **CycloneDX-SBOM** und Herkunftsnachweise für Artefakte. | geplant |
| **Dependabot-Wartezeit** für LLM-SDKs. | geplant |
| **Ein zweiter Secret-Scanner in der CI** (das gitleaks-Programm, festgelegt und per Prüfsumme geprüft) und GitHub-Push-Schutz. | geplant |
| **Signierte Updates** des Frameworks selbst (`orchestrator/utils/updater.py`, in Entwicklung: Update-Prüfung, Update und Neustart; noch nicht eingebunden), fertig gebaut als geprüfte Updates einer signierten Veröffentlichung statt eines einfachen `git pull`. Werkzeugketten-Images gehören nicht dazu: Das Framework teilt Rezepte, nie kompilierte Images, daher werden sie weiter auf deinem Rechner aus dem Dockerfile des Targets gebaut; die Einstellungen `docker_registry` (standardmäßig leer) und `docker_namespace`, die nichts liest, bleiben erhalten. Keine Vorgabe nennt ein Image aus einer Registry: Die Image-Einstellungen nennen lokale Images (`llm-framework/...`) oder Images Dritter wie Trivy und Qdrant, und der Block `docker` in der `target.yml` eines Targets nennt ein lokales Image ohne Registry. | geplant |

## Rust-Teile (geplant)

Der Kern bleibt Python. Teile, die für sich an einer Vertrauensgrenze stehen und noch nicht gebaut sind, werden in
Rust geschrieben; andere Teile wechseln nur, wenn eine Messung einen Gewinn zeigt (Entscheidung des Maintainers vom
2026-10-16).

| Punkt | Status |
|---|---|
| **Netzwerk-Gateway für MCP in Rust.** Ein Gateway-Prozess vor dem Python-MCP-Server für Clients auf anderen Geräten: TLS und Anmeldung per OAuth, benanntem Token oder vorab geteiltem Schlüssel (eine HMAC-Signatur bei jeder Anfrage). Warum: Dieser Teil steht zum Netzwerk hin; Rust gibt ihm Speichersicherheit und einen kleinen, schnellen Prozess. | geplant |
| **MQTT-Broker im Gateway (rumqttd).** Er weckt KI-Clients nur mit festen Stichwörtern (die Einzelheiten kommen über MCP), und andere Anwendungen können ihn abonnieren und eigene Feeds hinzufügen. Er ersetzt den amqtt-Broker, der im Python-Prozess geplant war. Warum: kein zweiter Server, und der Broker sitzt dort, wo die Netzwerkverbindungen ankommen. | geplant |
| **Root-Helfer auf dem Board in Rust**: ein statisches Programm mit festen Unterbefehlen für das, was Build und Deploy brauchen, und eine sudoers-Regel nur für diesen Helfer. Warum: kein allgemeines sudo auf dem Board, und ein statisches Programm läuft auf jedem Board, egal welche C-Bibliothek es hat. | geplant |
| **Die Rust-Teile des Frameworks fertig gebaut und signiert ausgeliefert.** Die CI macht sie zu Release-Dateien dieses Repositorys und signiert sie schlüssellos mit Sigstore; ihre Prüfsummen liegen im Repository, und der Installer prüft Signatur und Prüfsumme, bevor er ein Programm nutzt. Selbst bauen bleibt als Opt-in möglich. Warum: Auf deinem Rechner ist keine Rust-Werkzeugkette nötig, und jedes Programm bleibt prüfbar. | geplant |

- **Keine fertigen Docker-Images für Targets.** Target-Images werden weiter auf deinem Rechner aus dem Dockerfile des
  Targets gebaut und nie in eine Registry geladen. Warum: Das Framework teilt Rezepte, keine Images, und ein Image
  müsste für jedes Update neu gebaut werden.
- **Kein Go.** Warum: Eine kompilierte Sprache neben Python reicht; eine zweite brächte eine zweite Werkzeugkette.
- **Die GUI bleibt Qt (PySide6).** Warum: Sie läuft unter Windows und Linux, und Qt selbst ist nativer Code.

## Später (geplant)

- **Mehr Targets und Formate**, als Target-Vorlagen und über ein Beitragspaket:
  - **Modelle der Llama-Familie in jedem Format.** Warum: Das Framework begann mit RKNN, und Llama-Modelle müssen auf
    jedem Target laufen.
  - **ONNX, mit einer Vorstufe von Hugging Face nach ONNX** für NPU-Werkzeugketten (RKNN, Hailo, Intel NPU), in einer
    eigenen festgelegten Umgebung. Warum: Heute braucht RKNN eine lokale `.onnx`-Datei, die ein Laie nicht erstellen
    kann.
  - **NVIDIA Jetson** (das RTX-GPU-Target steht in 3.2.0). Warum: eine verbreitete Edge-Plattform mit eigener
    Werkzeugkette.
  - **Hailo.** Warum: eine verbreitete NPU-Erweiterung, etwa für den Raspberry Pi 5. Zuerst Hailo-8 auf einem Board der
    Klasse Raspberry Pi 5 (Hailo-10 experimentell, bis ein Lauf besteht); der zugangsbeschränkte Dataflow Compiler wird
    nie mitgeliefert, nur gefunden und genutzt, wo die Person ihn hat (Runde 24).
  - **Intel** NPU und GPU. Warum: Viele kleine PCs haben eine.
  - **Qualcomm.** Warum: Viele ARM-Boards und Laptops haben seine NPU.
  - **Hersteller-SDK-Dateien, die du bereitstellst:** eine Datei, die du hinter einer Hersteller-Anmeldung
    heruntergeladen hast, geprüft per sha256 und Größe; du nimmst ihre Lizenz an, und Pakete tragen nur einen Verweis,
    nie die Datei. Warum: Hailo und Qualcomm geben Teile ihrer SDKs nur nach einer Registrierung heraus.
- **Später nützlich**, aus den Notizen des Maintainers und der Lückenprüfung:
  - **Versionsbewusstes Wissen:** Auszüge mit Target-, Toolkit- und Treiberversion markiert, Suchen danach gefiltert,
    und Doku-Quellen mit ihrer SDK-Version in der `target.yml`. Warum: Antworten für verschiedene SDK-Versionen dürfen
    sich nicht mischen.
  - **Code-Signatur** für den Windows-Installer und die Skripte, zuerst die kostenlosen Wege für Open Source. Warum:
    keine Warnung „unbekannter Herausgeber“ und kein `ExecutionPolicy Bypass`.
  - **„Problem melden“** nach einem unbekannten Build-Fehler: ein vorausgefülltes Issue mit der datenschutzbereinigten
    Probe, dir zuerst gezeigt und im Browser geöffnet, nie von selbst gesendet. Warum: Der Maintainer kann Boards, die
    er nicht besitzt, nicht nachstellen.
  - **Ein Hinweis auf eine neuere llama.cpp-Version** (eine Mojo-Zeile oder ein geplanter CI-Job); die Festlegung
    verschiebt weiter ein Mensch. Warum: llama.cpp ändert sich schnell, und die Festlegung bleibt eine bewusste Wahl.
  - **Lint erzeugter Module** (ShellCheck, hadolint), mit den Funden zurück an die KI, die sie erstellt hat. Warum:
    Fehler werden vor dem ersten Build behoben.
  - **Trivy-Funde als JSON** (Paket, CVE, Schweregrad, behobene Version) im Ergebnis und im Build-Datensatz. Warum: Du
    und die KI seht die Funde, nicht nur Log-Zeilen.
  - **Community-CI-Builds** beigetragener Targets (Image-Build ohne Push, Smoke-Test, Trivy auf dem Archiv). Warum:
    Ein Beitrag muss funktionieren, nicht nur die Prüfungen bestehen.
  - **Jedes Fenster übersetzt, mit einem Sprachmenü** (Menüs, Reiter, Dialoge, der Hub, das Deployment-Fenster).
    Warum: Viele Fenster sind noch nur englisch.
  - **Ein QEMU-Smoke-Test von Cross-Builds in der CI**, nie auf deinem Rechner. Warum: Ein kaputter Cross-Build fällt
    auf, bevor er ein Board erreicht.
  - **Ein Schritt „alle Dateien bearbeiten“ im Modul-Assistenten**, mit Diff und erneuten Prüfungen. Warum:
    Herstellerpfade und Flags lassen sich fertigstellen, bevor das Modul gespeichert wird.
  - **Ein Verlauf der Heilungs-Korrekturen** mit ihrer Erfolgsquote in Mojo; eine Korrektur wird nie von selbst
    angewendet. Warum: Er zeigt, welche Korrekturen wirklich helfen.
  - **Ein Feld für den Zweck** (Chat, Code, Zusammenfassungen, Embeddings) für die Quantisierungsempfehlung. Warum:
    Der beste Typ hängt vom Einsatz ab.
  - **Eine optionale Ollama-Modelfile** neben einer GGUF. Warum: Ollama kann das Ergebnis dann direkt laden.
  - **Eine Anleitung für Edge-Einsätze:** dein deploytes Modell in anderen Programmen (seine OpenAI-kompatible API,
    lokales RAG, Sprache). Warum: Der Schritt nach dem Deployment ist noch nicht beschrieben.
  - **Boards im Netz finden** (Zeroconf), mit unveränderten Regeln für Host-Schlüssel. Warum: Du musst die Adresse
    des Boards nicht kennen.
- **Crawls großer Doku-Seiten mit Fortsetzen**: einen gestoppten Crawl später fortsetzen statt nur eines Seitenlimits.
- **Eine Reranker-Schwelle für Stichwortsuchen**: die Relevanzschwelle für kurze Anfragen aus Stichwörtern, etwa
  die Suchanfrage des Modulvorschlags, mit der Abruf-Evaluation kalibrieren, damit auch diese Suchen neu geordnet
  werden können.
- **Messung der GPU-Stufe**: `BAAI/bge-m3` und `bge-reranker-v2-m3`, die mit CUDA automatisch gewählt werden, mit
  der Abruf-Evaluation auf einem Rechner mit CUDA bewerten.
- **Anmeldewege, die Registrierungen brauchen**: AWS IAM Identity Center mit eigenem registriertem Client, GitHub
  Copilot über eine eigene GitHub-App, Gemini mit dem eigenen OAuth-Client des Benutzers.
- **Lokaler API-Server** (Einstellungen `api_enabled`, `api_host`, `api_port`, in Entwicklung).
- **GUI-Design und automatische Aktualisierung** (Einstellungen `gui_theme`, `gui_auto_refresh`,
  `gui_refresh_interval`, in Entwicklung).
- **Aufräumen** (Einstellungen `auto_cleanup` und `logs_dir`, in Entwicklung): Arbeitsordner nach Builds aufräumen und
  ein einstellbarer Log-Ordner.
- **MCP-Apps-Oberfläche.**

Nicht geplant: Builds in der Cloud (AWS, Azure) oder auf gemieteten Maschinen. Kompilieren auf entfernten Rechnern war
nie geplant; kommen je Builds auf einem anderen Rechner, baut das Framework sie selbst.
