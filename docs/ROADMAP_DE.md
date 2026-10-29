Version 3.0.0 + unveröffentlichte Änderungen · Stand 2026-10-29

[English](ROADMAP.md) · **Deutsch**

# Roadmap

Was als Nächstes kommt. Versionsnummern und Inhalte können sich noch ändern; nichts in diesem Dokument ist Teil
einer veröffentlichten Version. Es ist eine der drei Dateien des [Bauplans](BLUEPRINT_DE.md): Wie das Framework
gebaut ist, steht im [Design](DESIGN_DE.md). Die neueste Veröffentlichung ist 3.0.0 (2026-10-10, siehe
[Changelog](../CHANGELOG_DE.md)).

Status je Punkt:

- **fertig**: gebaut und im Changelog unter „Unreleased“ aufgeführt; Teil der nächsten Veröffentlichung.
- **in Arbeit**: wird gerade gebaut.
- **geplant**: beschlossen, noch nicht begonnen.

Wird eine Version veröffentlicht, verlassen ihre fertigen Punkte die Roadmap und erscheinen im Changelog unter
dieser Version.

| Version | Thema | Status |
|---|---|---|
| 3.1.0 | Ditto-Neubau, Anmeldung und lokale KI | in Arbeit |
| als Nächstes | Build-Dienst, Warteschlange und Modi | geplant |
| 3.2.0 | Laufzeit auf dem Gerät und die Hardware des Maintainers | geplant |
| 3.3.0 | LoRA und große Modelle | geplant |
| 3.4.0 | Lieferkette und Community | geplant |
| später | Rust-Teile für das Netzwerk und das Board | geplant |
| später | Mehr Hardware und MCP-Erweiterungen | geplant |

## 3.1.0 - Ditto-Neubau, Anmeldung und lokale KI (in Arbeit)

| Punkt | Status |
|---|---|
| **Ditto auf eigener Anbieterschicht neu gebaut.** Offizielle SDKs `anthropic` und `openai` statt LiteLLM; Anbieter als Daten (`configs/llm_providers.yml`) mit Modell und Aufwand je Aufgabe und einer live geladenen Modellliste; Wiederholungen, Fallback-Kette, geprüfte strukturierte Antworten, Prompt-Caching, Datenschutzfilter für Cloud-Anbieter; Ditto nutzt die Tools des Frameworks im Rahmen deiner Berechtigungen (GUI-Chat und `llm-cli ditto ask`). | fertig |
| **Lokale Wissensbasis.** FastEmbed-Embeddings auf der CPU, im Prozess eingebettetes Qdrant, hybride Suche (BM25 plus dichte Vektoren, Reciprocal Rank Fusion); kein Vektordatenbank-Server und keine Cloud-Embeddings. | fertig |
| **Wissensbasis in Qdrant Edge.** Die Rust-Engine von Qdrant im Prozess statt des lokalen Modus von qdrant-client; eine ältere Wissensbasis wird bei der ersten Nutzung umgewandelt und geprüft, ihr alter Ordner bleibt, bis du ihn löschst (siehe [Der Speicher der Wissensbasis](DESIGN_DE.md#der-speicher-der-wissensbasis-qdrant-edge-unveröffentlicht)). | fertig |
| **Verwaltetes lokales Modell mit Stufen bis hinunter zu 2 Bit.** llama.cpp-`llama-server`, auf deinem Rechner aus einem festgelegten Commit selbst gebaut (Linux: CPU, dann Vulkan; siehe die selbst gebaute Laufzeit unten), das offizielle vorgebaute Archiv (CUDA, Vulkan, Metal oder CPU; ROCm nur auf Wunsch) nur als ausdrücklicher Opt-in `--prebuilt`, gekennzeichnet mit „not self-built“, und festgelegte Qwen3.5-Modelle von 9B bis hinunter zu 2-Bit-Modellen für schwache Hardware (3 GB RAM); `llm-cli ditto runtime` und `llm-cli ditto models`. | fertig |
| **Selbst gebautes llama.cpp, Stufe 1: eine Hardware-Probe.** Probe-Format Schema 2 mit der Python-Probe als Hauptweg und den Skripten `.sh` und `.ps1` als Rückfallwege, die dieselben Schlüssel schreiben: Betriebssystem, Version, Init-System, C-Bibliothek und ihre Version, Architektur und CPU-Merkmale des Rechners, der die Programme ausführt, auf einem Board per SSH gelesen, ohne dort etwas zu schreiben; keine Hostnamen, Serien- oder Teilenummern in Profilen. | fertig |
| **Selbst gebautes llama.cpp, Stufe 1: ein Rezept, per Commit festgelegt, ohne Netz gebaut.** Ein Build-Rezept macht aus einem Profil einen Bauplan mit Build-ID; llama.cpp ist an einer Stelle per Commit festgelegt, die Target-Images, Vorlagen und der Katalog holen und prüfen (aus 3.4.0 vorgezogen); der Quelltext wird einmal geholt und geprüft und als Bundle auf Rechner ohne Netz gebracht; Konfiguration und Build laufen nativ oder im Builder-Container ohne Netz; jeder Build wird geprüft (Format, glibc-Symbolversionen, Version und Commit, Geräte, der Server lehnt `--tools` ab) und in einem Build-Speicher abgelegt; ein Build von einem deiner anderen Rechner kann importiert werden. | fertig |
| **Selbst gebautes llama.cpp, Stufe 1: Konvertierungswerkzeuge abgestimmt auf den Build-Rechner.** Target-Images bauen `llama-quantize`, `llama-imatrix`, `llama-perplexity`, `llama-completion` und `llama-bench` nach dem Rezept für die CPU des Build-Rechners; neue Target-Module bekommen einen Block `runtime:`; Target-Module bauen llama.cpp aus dem Quelltext und beziehen nie vorgebaute llama.cpp-Images oder -Programme. | fertig |
| **Selbst gebautes llama.cpp, Stufe 1: Ditto auf dem selbst gebauten Server (Linux).** `llm-cli ditto runtime install` baut `llama-server` (zuerst CPU, dann den GPU-Build), statt ihn herunterzuladen; eine Startregel (verbotene Optionen in jeder Schreibweise, eine geleerte Umgebung, Loopback und eine Schlüsseldatei) für jedes Programm, das das Framework startet, auf dem PC und auf einem Board; die Perplexity-Prüfung nutzt das selbst gebaute `llama-perplexity` ohne geholtes Image. | fertig |
| **Selbst gebautes llama.cpp, Stufe 1: die Laufzeit auf dem Board.** `llama-server`, `llama-cli`, `llama-bench` und `llama-perplexity`, aus der eigenen Probe des Boards im Builder-Container cross-kompiliert (Basis passend zur glibc oder musl des Boards, C++-Laufzeit statisch gelinkt, `libgomp.so.1` dabei), als Tarball mit Startskript gepackt, per SSH als unprivilegierter Benutzer deployt und auf dem Board geprüft, bevor er aktiv wird; Startskript oder systemd-Unit vorher in den Deploy-Einstellungen gewählt. Getestet mit Nachbildungen und einem lokal nachgebildeten Board, noch nicht auf einem echten Board. | fertig |
| **Deployment auf ein Board über ein Netzwerk, das wegbricht.** Adresse oder Hostname; das Deployment wartet auf ein Board, dessen WLAN wegbricht (sichtbares Warten, Grenze, Abbrechen), und setzt den Upload nach einer sha256-Prüfung fort, `deploy.sh` läuft auf dem Board für sich und wird einmal gestartet; vier SSH-Grund-Codes mit nächsten Schritten auf Englisch und Deutsch und reinen Codes für KIs; der KV-Cache (`f16`, `q8_0`, `q4_0`) in den Deploy-Einstellungen, automatisch `q8_0` auf Boards mit wenig Speicher; für ein Board gebaute Modelle werden auf dem Board gemessen (oder in der Docker-Sandbox), nie nativ auf diesem Computer, und ein bestandener Lauf auf dem Board wird beim Build als „getestet auf <Board>“ vermerkt. | fertig |
| **Ditto-Evaluation.** `llm-cli ditto eval` mit festen, nach Regeln bewerteten Fällen, je Anbieter oder lokaler Modellstufe. Die erste Messung hat Lücken in Dittos Prompts gefunden; sie sind behoben, eine neue Messung läuft. | fertig |
| **Anmeldung je Zugangsart** für alle gängigen Cloud- und API-Anbieter und für lokale Engines (Einzelheiten unten). | in Arbeit |
| **Entfernung des Anbieters `claude_code`.** Anthropic erlaubt Drittanbieter-Apps die Anmeldung mit einem Claude-Abo nicht. Wer ein Claude-Abo hat, nutzt das Framework aus Claude Code oder Claude Desktop über den MCP-Server (`llm-cli mcp config` zeigt die Einrichtung) oder gibt Ditto einen Anthropic-API-Schlüssel; die Tarife Max und Team enthalten monatliches API-Guthaben. | fertig |
| **Das Framework in einem Container, lokal gebaut.** Kein Framework-Image wird veröffentlicht oder geladen: `scripts/build_container_image.sh` (`make build`) baut das Image auf deinem Computer, indem es aus den ausgelieferten Dateien dieselbe Installation ausführt wie auf dem Rechner selbst (`install.sh`); standardmäßig CLI und MCP-Server ohne Oberfläche, die GUI nur als Option; eine funktionierende Compose-Datei, Makefile-Ziele und eine Bauanleitung ([Container-Einrichtung](Container_Setup_DE.md)). | fertig |
| **Dokumentation auf Englisch und Deutsch** für jedes Dokument. | in Arbeit |
| **Crawler, der die eigenen Signale der Seite prüft.** Ob eine Seite ausgewertet werden darf, kann ein Nutzer nicht wissen. Deshalb prüft der Crawler die maschinenlesbaren Nutzungsvorbehalte selbst (robots.txt nach RFC 9309, den Header `X-Robots-Tag`, Robots-Meta-Tags einschließlich `noai` und seines eigenen Bot-Namens, das TDM Reservation Protocol) und überspringt vorbehaltene Seiten mit Nennung im Log; jeder Vorbehalt gewinnt, und keine Prüfung lässt sich abschalten. Statt einer rechtlichen Bestätigung erklärt ein einmaliger Hinweis (je Benutzer, erneut, wenn sich sein Text ändert), was gelesen und behalten wird, mit Sprachansage über die Sprachausgabe des Betriebssystems (die Technik des USB-Watchers aus rag4ll); ein Stopp-Knopf behält das Gespeicherte, der Fortschritt zeigt „Seite N von M“, das Ergebnis nennt ehrlich Erfolg, Teilerfolg oder Fehler, und der Crawler meldet seine echte Version. Eine Person startet Deep Ingest in der GUI oder bestätigt das `crawl_docs_site` eines KI-Clients (unveröffentlicht). | fertig |
| **Eingabe-Historie**: zuletzt verwendete Modelle, Targets, Boards, URLs, Geräte und Datensätze als Auswahl in den GUI-Feldern, je Benutzer, ohne Geheimnisse; Tools > Eingabeverlauf löschen... vergisst sie. | fertig |
| **Schnellerer Start durch Lazy Loading.** CLI, MCP-Server und GUI laden einen schweren Teil erst, wenn ein Befehl ihn zum ersten Mal braucht: die Manager des Frameworks (nicht für Befehle, die kein Framework brauchen), die Wissensbasis und den Crawler, Qt außerhalb der GUI, die Prüfungen von Mojo (auch seine Prüfung der Boards), die Brücke des MCP-Servers zum offenen Fenster (beim ersten Aufruf seines Werkzeugs `gui`), die Lizenztexte der Wissenspakete (mit dem ersten Paket), rich für `--help`. Der Import der URL-Prüfungen prüft den Host nicht mehr (Mojo meldet die Python-Version und den freien Speicherplatz). Warum: `llm-cli mcp serve` muss einem KI-Client schnell antworten, und `llm-cli --help` soll nicht alles laden. | fertig |
| **Die GUI ist ein optionales Extra.** PySide6 ist das Extra `gui` (`requirements-gui.txt`): Die Desktop-Installer installieren es, `install.sh --headless` und das Framework-Image ohne `--gui` lassen Qt und seine Systembibliotheken weg, und Kommandozeile und MCP-Server laden nie Qt (die Texte des Kerns kommen ohne Qt aus). `llm-gui` ohne das Extra sagt, wie du es ergänzt. Qts eigene Schaltflächen (Schließen, Abbrechen) folgen der Sprache des Fensters. Warum: Ein Server oder Container braucht keine Desktop-Bibliotheken. | fertig |
| **CPU und Speicher des Build-Containers im Builder**, und eine Warnung im Build-Protokoll, bevor dem Container der Speicher ausgeht (die Grenze von Docker oder der VM von WSL2 / Docker Desktop). Warum: Ein Build kann den Speicher von Docker oder WSL2 aufbrauchen. | fertig |
| **Bessere Antworten aus der Wissensbasis**, aus rag4ll übernommen und klein gehalten: Reranking mit einem Cross-Encoder auf der CPU und eine Relevanzschwelle, die unpassende Auszüge aus Dittos Prompt heraushält (eingeschaltet für Build-Empfehlungen; für Chat und MCP-Suchen mit `rag_rerank_default`), ein Größenbudget für die Auszüge, das Aufteilen übergroßer Code- und Tabellenblöcke, eine Stichwortsuche, die Bezeichner wie `-DGGML_CUDA=ON` versteht, und das Entladen unbenutzter Modelle; gemessen mit der Abruf-Evaluation (`llm-cli ditto eval --kind retrieval`). | fertig |
| **Community-Pakete mit echter Kryptografie**: Pakete mit einem Manifest jeder Datei, öffentliche Pakete mit Ed25519 signiert, private Pakete im Format age (rage, über `pyrage`) an die Schlüssel der Empfänger oder eine Passphrase verschlüsselt, vom Absender signiert und mit den üblichen age-Werkzeugen lesbar. Der Upload mit der XOR-„SwarmCipher“ und seine Abfrage eines GitHub-Tokens entfallen; das Framework fragt nie nach einem Token. | fertig |
| **Model-Zoo aus der Community**: Rezepte (Modell mit festgelegtem Commit, Target-Fingerabdruck, Format, Quantisierung, Toolchain-Image und geprüfte Hashes), aus fertigen Builds erzeugt; ein signierter Index mit Widerrufen, Installation über eine Quarantäne mit Vertrauensstufen, `llm-cli zoo`, der Community-Hub, der Export für die Community mit Übergabe an einen Pull-Request und die Werkzeuge des Maintainers für das Community-Repository. | fertig |
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

## 3.2.0 - Laufzeit auf dem Gerät und die Hardware des Maintainers (geplant)

Die eigene Hardware des Maintainers setzt die Prioritäten: Radxa ZERO 3W (RK3566), NVIDIA RTX 2080 Ti und RTX 3090,
AMD Ryzen Z1 Extreme, unter Windows und Linux. Die Rezepte kommen zuerst; die Rotationsstufe und das allgemeine
ARM64-Target folgen direkt danach.

| Punkt | Status |
|---|---|
| **Rezepte als Target-Vorlagen.** Eine Target-Vorlage enthält den Build-Code; Modell, Quantisierung, Format und Deployment sind Variablen, die du im Build-Menü wählst, und der Builder nutzt alles, was ein Rezept enthält. Warum: Du bist nie an die Wahl der Person gebunden, die die Vorlage gemacht hat. | geplant |
| **Rotationsstufe vor der Quantisierung** (SpinQuant, QuaRot), direkt nach den Rezepten: Rotationen werden vor Konvertierung und Quantisierung in die Gewichte eingerechnet, nur wo das Zielformat sie unterstützt (eine Fähigkeit des Formats, kein fester Schritt). Warum: 4-Bit-Modelle der Llama-Familie behalten auf kleinen Boards ihre Qualität. Der Platz ist da: Der Stage-Runner führt das `modules/rotate_module.sh` eines Targets zwischen den Stufen convert und target aus; der Algorithmus kommt noch. | geplant |
| **Allgemeines ARM64-CPU-Target (GGUF)** mit Board-Vorgaben für den Raspberry Pi 4 und 5, direkt nach den Rezepten. Warum: Rockchip war nur der Anfang, und diese Boards brauchen kein Hersteller-SDK. | geplant |
| **Targets aus der Hardware-Probe, auf dem Board bewiesen.** Ein neues Target beginnt mit der Probe, die du auf dem Board ausführst (auch ohne Netz, die Datei zurückgetragen), oder, wenn du willst, mit Ditto, der es über SSH prüft; ein bestandener Benchmark auf dem Board ist der Beweis, gezeigt als „getestet auf <Board>“ im Community-Hub und in Paketen mitgegeben. Warum: Der Maintainer besitzt die meisten Boards nicht, und nur ein Lauf auf dem echten Board zeigt, dass ein Target funktioniert. | geplant |
| **Laufzeit auf dem Gerät, der Rest.** Der erste Lauf der selbst gebauten Board-Laufzeit auf einem echten Radxa ZERO 3W; die Geräte-Laufzeit in Deployment-Paketen; das Laufzeit-Image für Docker-Deployments, lokal aus einem Geräte-Build gebaut (statt des geholten Laufzeit-Images); Benchmarks auf dem Gerät und eine gemessene Quantisierungsempfehlung je Board; ein Build auf dem Board, wenn keine Builder-Basis alt genug für seine C-Bibliothek ist; Vulkan auf Mali erst, wenn llama-bench zeigt, dass es den CPU-Build schlägt; MCP-Tools zum Proben, Deployen und Messen von Geräten. | geplant |
| **Selbst gebautes llama.cpp, Stufe 2.** CUDA-Builds (die Redist-Archive von NVIDIA, das Toolkit nach Treiber, Compute Capability, Host-Compiler und CMake gewählt), Windows-Builds (Clang für das CPU-Backend, MSVC nur als CUDA-Host-Compiler; bis dahin Dittos lokales Modell unter Windows und macOS nur über den Opt-in `--prebuilt`), macOS (ungetestet), Benchmarks auf dem PC, die Dittos Modellstufe wählen, und MCP-Tools für die Laufzeit. | geplant |
| **Weitere Rockchip-SoCs für RKLLM** (RK3562, RV1126B) mit einer Tabelle der unterstützten Typen je SoC. | geplant |
| **NVIDIA-GPU-Target**: GGUF mit CUDA für die RTX 2080 Ti und RTX 3090 (CUDA-Version je nach Treiber), die vLLM-Formate AWQ und GPTQ, EXL3 und natives Deployment auf Windows-Rechner mit NVIDIA-GPU. | geplant |
| **GPU-Beschleunigung** für die Importance-Matrix und den RKLLM-Export. | geplant |
| **Target für AMD-RDNA3-APUs** für den Ryzen Z1 Extreme: llama.cpp mit Vulkan und HIP, Linux-Container und natives Deployment unter Windows. | geplant |
| **Prüfung vor dem Build** (`orchestrator/Core/consistency_manager.py`, in Entwicklung): vergleicht die Treiber, die die Hardware-Probe gefunden hat, mit dem, was das SDK des Targets braucht (etwa die CUDA- und Treiber-Matrix), und hält einen Build an, der nicht gelingen kann, bevor er startet; muss noch in den Build-Start eingebunden werden. | geplant |
| **Benchmark-Fenster** (`orchestrator/gui/benchmark_window.py`, in Entwicklung): llama-bench ausführen und die Ergebnisse als Tabelle zeigen; muss noch auf Basis der Laufzeit auf dem Gerät fertig gebaut und in der GUI erreichbar gemacht werden. | geplant |
| **Eine SSH-Verbindung für die Befehle eines Board-Schritts** (OpenSSH `ControlMaster`, als Option): Der OpenSSH-Rückfallweg meldet sich einmal je Schritt an statt einmal je Befehl; geteilte Verbindungen bleiben bis dahin aus, und die eigenen SSH-Einstellungen des Frameworks lehnen sie ab. | geplant |

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
| **Die vier Module sind die Build-Struktur** (das geplante Aufräumen der Vier-Module-Skripte, anders erledigt als geplant): Statt sie zu entfernen, hat die Entscheidung U9 des Maintainers sie zum Build gemacht. `build.sh` von Rockchip und der Vorlage ist ein Stage-Runner über `source_module.sh`, `config_module.sh`, `convert_module.sh` und `target_module.sh`, mit Marken zum Fortsetzen je Stufe, Fortschritt je Stufe und dem Platz für die Rotation; die Arbeit des Verteilers liegt in der Stufe, zu der sie gehört, und der Modulgenerator schreibt funktionierende Stufenmodule statt Platzhaltern. | fertig |
| **SSOT-Korrekturen** für den Katalog der Projektquellen und seine Skripte. | fertig |

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
  - **Hailo.** Warum: eine verbreitete NPU-Erweiterung, etwa für den Raspberry Pi 5.
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
- **Ein MCP-Server auf Edge-Geräten.**

Nicht geplant: Builds in der Cloud (AWS, Azure) oder auf gemieteten Maschinen. Kompilieren auf entfernten Rechnern war
nie geplant; kommen je Builds auf einem anderen Rechner, baut das Framework sie selbst.

