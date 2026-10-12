# Changelog

Alle nennenswerten Änderungen an diesem Projekt stehen in dieser Datei.
Englische Fassung: [CHANGELOG.md](CHANGELOG.md).

Das Format basiert auf [Keep a Changelog 1.1.0](https://keepachangelog.com/de/1.1.0/),
und dieses Projekt hält sich an [Semantic Versioning 2.0.0](https://semver.org/lang/de/spec/v2.0.0.html).
Geplante Arbeiten stehen in der [Roadmap](docs/BLUEPRINT_DE.md#roadmap), nicht hier.

## [Unreleased]

### Hinzugefügt
- **`start_recipe_build` und die Ressource `lcf://packs/index`** (MCP-Design T9): ein KI-Client baut ein
  installiertes Community-Rezept genau wie festgelegt (das Modell beim Commit des Rezepts, Target, Format, Board,
  Quantisierung, Importance-Matrix, Kontextlänge) über den zweistufigen Weg von `start_build` (dieselbe Frage,
  Speicherreservierung, Kontingente und derselbe Umfang für Daueraufträge; `BuildService.start_recipe`). Ein
  Kalibrierungsdatensatz wird nur genommen, wenn das Rezept einen nennt, und muss genau diese Datei sein; die Frage
  zeigt Importance-Matrix und Kontextlänge, und ein nach der Frage in irgendeinem Feld geändertes Rezept wird nicht
  gebaut. Die Ressource zeigt den zwischengespeicherten Index und die
  installierten Packs wie `list_packs`, nie aktualisiert.
- **Ein bestätigter Build gibt den Build-Dauerauftrag für Packs frei, die unter einem installiert wurden**
  (MCP-Design 6.10): ein Target- oder Rezept-Pack, das eine KI unter einem Dauerauftrag installiert hat, wird
  erfragt, bis die Person einen Build davon im eigenen Fenster des Frameworks bestätigt (eine Antwort per
  Elicitation, die Nutzung eines Dauerauftrags oder „erlauben“ zählen nie); das Framework vermerkt diesen Build
  (`confirmed_builds.json` im Benutzerordner), und der Build-Dauerauftrag deckt die folgenden Builds ab. Ein per
  Digest gepinntes Basis-Image außerhalb der Docker Official Images zählt, sobald ein Build mit genau diesem Image
  bestätigt wurde; ein Tag nie. Die Regelversion des Build-Dauerauftrags ist jetzt 2, Daueraufträge nach der
  engeren Regel verfallen.
- **Dittos MCP-Client-Modus in der GUI** (MCP-Entwurf T12): Configure AI Agent (Ditto) hat einen Abschnitt
  „Ditto's tools“ mit Tool-Quelle, einem Befehl (stdio) oder einer Serveradresse (HTTP, nur https oder Loopback),
  einem Namen und dem benannten Token, das im Tresor für Zugangsdaten landet und nie wieder angezeigt wird; den Modus
  ohne Token, mit einem nicht lesbaren Befehl oder mit einer Adresse zu speichern, die das Token im Klartext senden
  würde, wird abgelehnt und speichert nichts. Dittos GUI-Bestätigung bietet „Allow and ask it to stop asking...“ für
  die Rückfrage eines entfernten Frameworks nur an, wenn das Formular jenes Servers es anbietet, mit einer Warnung
  vorab; es ist nur eine Anfrage, die eine Person im eigenen Freigabefenster jenes Frameworks bestätigt.
- **Dittos Aufgaben nutzen die Aufgaben-Prompts des entfernten Frameworks** (MCP-Entwurf 4.3, T12): Im
  MCP-Client-Modus holen Build-Empfehlung, Modulvorschlag und die Analyse eines entfernten Builds über seine
  Request-ID ihren Kontext (Formate des Targets, Build-Log, Auszüge aus Dokumentation und Wissensbasis) aus den
  MCP-Prompts `recommend_build_settings`, `add_board` und `diagnose_build_failure` jenes Frameworks, unter Dittos
  eigenem Aufgabentext, als nicht vertrauenswürdige Daten; ein Prompt, den der Server nicht anbietet, wird mit dem
  nächsten Schritt genannt.
- **Das Ende eines Deployments im Audit-Log**: Endet ein `deploy_to_device`-Job (erledigt, gescheitert oder
  abgebrochen), wird ein eigener Abschlusseintrag mit Job, Gerätenamen und Gerätezustand geschrieben, verknüpft mit dem
  `started`-Eintrag des Aufrufs.
- **Optionale Tools `delete_artifact` und `restore_knowledge_snapshot`** (MCP-Entwurf T10): ein gebautes Modell
  (Ordner und Golden-ZIP) per ID und sha256 löschen oder die Wissensbasis als Job durch einen früheren Snapshot
  ersetzen. Beide zerstören etwas: standardmäßig verboten und verborgen, bis du sie freigibst (die ganze Klasse
  „extern“ zu erlauben genügt nicht), jeder Aufruf mit Frage, nie ein Dauerauftrag. Ein Löschen wird abgelehnt
  außerhalb des Ausgabeordners, solange ein Build irgendeines Programms des Frameworks in den Ordner schreibt oder
  dafür wartet, nachdem sich der Build geändert hat und wenn der Ordner nicht der ist, den die Frage nannte. Eine
  Wiederherstellung sichert immer zuerst die aktuelle Wissensbasis (`pre_restore_<Zeit>`), auch außerhalb von MCP,
  und räumt den wiederherzustellenden Snapshot nie auf.
- **Ditto als MCP-Client eines anderen Frameworks** (MCP-Entwurf T12; Ditto ist hybrid): `llm-cli ditto remote set`
  richtet Ditto auf den MCP-Server eines Frameworks aus, das anderswo läuft, zum Beispiel im Headless-Docker-Image
  (stdio über einen Befehl wie `docker exec -i <container> llm-cli mcp serve` oder HTTP an eine https- oder
  Loopback-Adresse). Es gelten Registry, Berechtigungen, narrensichere Schicht, Rückfragen und Audit-Log jenes
  Servers; Dittos Identität dort ist ein benanntes Token, angelegt mit `llm-cli mcp token add NAME --link ditto`,
  sodass Dittos eigene Berechtigungen in beiden Modi zugleich gelten und das Audit-Log dort `mcp:NAME` zeigt. Das
  Token liegt in Dittos Tresor für Zugangsdaten und erreicht den Server nur als `LCF_MCP_TOKEN` oder als
  Bearer-Header; ohne Token startet der Modus nicht. Eine Tool-Quelle pro Unterhaltung; jedes entfernte Ergebnis,
  jeder Fehler, Prompt und jede Tool-Beschreibung sind für Dittos Modell nicht vertrauenswürdige Daten; Dittos
  Bestätigung nennt den Server. `llm-cli ditto remote show|test|off`. `llm-cli mcp serve --transport http --container`
  bindet nur innerhalb eines Containers dessen Schnittstellen (benanntes Token, Host- und Origin-Prüfung unverändert;
  den Port auf dem Loopback des Hosts veröffentlichen).
- **Gemeinsamer Build-Zustand** (MCP-Entwurf T11): Jedes Programm des Frameworks (die GUI, ein CLI-Aufruf, jeder
  MCP-Server) schreibt eine Zusammenfassung jeder Build-Anfrage in den Benutzerordner (`build_requests/`), sodass die
  Statusleiste der GUI, `llm-cli build status`, `list_build_requests` und `get_build_status` dieselben Builds sehen,
  gleich welches Programm sie gestartet hat, auch eine Anfrage, die noch in der Warteschlange eines anderen Programms
  wartet. Jede Zusammenfassung nennt das Modell mit Repo-ID oder Label (nie einen Host-Pfad), die Aufruferklasse, die
  sie gestartet hat (nie einen MCP-Sitzungsschlüssel), und das Programm, das sie ausführt (`started_in`, Spalte
  `Where`); Fehler sind geschwärzt. Eine Anfrage, deren Programm endete, bevor sie fertig war, erscheint als
  unterbrochen. Builds eines anderen Programms bleiben nur lesbar. Der Hinweis, nur Builds dieses Prozesses seien
  sichtbar, entfällt.
- **Ditto nutzt die Tools des Frameworks**: Im Chat (GUI) und mit `llm-cli ditto ask "<Frage>"` kann Ditto
  Targets auflisten, Capabilities prüfen, Builds starten und abbrechen, Logs lesen, die Wissensbasis durchsuchen
  und den Doctor ausführen, immer im Rahmen deiner Tool-Berechtigungen. Tools, die eine Bestätigung brauchen,
  zeigen ihren Titel und jedes Argument (ein Dialog in der GUI, eine Abfrage in der CLI; ohne Terminal wird
  abgelehnt).
- **KI-Einstellungen**: Anbieter aus `configs/llm_providers.yml`, eine Modellliste, die live beim Anbieter
  abgerufen wird (freie Eingabe geht weiterhin), Aufwand, Datenschutzstufe für Cloud-Anbieter, Offline-Modus und
  eine Ersatzkette.
- **Anbieterschicht für KI** (`orchestrator/llm`): ein Client für alle Anbieter, gebaut auf den offiziellen SDKs
  `anthropic` und `openai` (OpenAI-kompatible Server über `openai`). Anbieter sind Daten
  (`configs/llm_providers.yml`): Modell und Aufwand je Aufgabe, Wiederholungen, eine Ersatzkette, strukturierte
  Antworten, die gegen Schemas geprüft werden, und ein Datenschutzfilter für Cloud-Anbieter (Einstellung
  `ai_security_level`): Secrets werden immer geschwärzt; Adressen, E-Mail-Adressen, Logins, Benutzernamen,
  Hostnamen und Seriennummern werden durch stabile Platzhalter ersetzt, die in der Antwort lokal zurückübersetzt
  werden.
- **Lokale Wissensbasis** auf lokalen Embeddings (FastEmbed, ONNX auf der CPU) und einem eingebetteten
  Qdrant-Speicher: kein Vektordatenbank-Server und keine Cloud-Embeddings nötig, und sie funktioniert offline,
  sobald ihre Modelle heruntergeladen sind.
- **Die Wissensbasis, die Ditto nutzt, für KI-Clients** (MCP-Tools `list_knowledge_sources` und
  `read_knowledge_source`, Ressource `lcf://knowledge/sources/<Quellen-ID>`): Ein Client, der Dittos Aufgabe
  übernimmt, sieht, was die Wissensbasis enthält, und liest die Auszüge einer Quelle der Reihe nach.
  `search_knowledge` gibt jedem Auszug jetzt seine Herkunft mit (Quellen-ID, Adresse oder Pack, Titel, Abschnitt, Art
  `web`/`pack`/`docs`, Vertrauensstufe, Aufnahmedatum, Position) und nimmt `kind` und `rerank`. Quellen werden über
  IDs benannt, nie über Pfade; ihr ganzer Text ist als nicht vertrauenswürdig markiert. Ein Tool-Aufruf lädt nie das
  Embedding- oder Reranking-Modell herunter: Ohne Embedding-Modell wird die Suche mit dem nächsten Schritt abgelehnt,
  statt einen Download von etwa einem Gigabyte zu starten, und die Ablehnung nennt keinen Ordner.
- **Deep Ingest für KI-Clients** (MCP-Tool `crawl_docs_site`, Entscheidung vom 11.10.2026): Ein KI-Client oder Ditto
  kann Dokumentations-Websites in die Wissensbasis aufnehmen, als Job, mit genau dem Crawler und den Opt-out-Prüfungen
  der GUI (robots.txt, Robots-Anweisungen einschließlich `noai`, TDMRep). Du wirst jedes Mal gefragt (nie „nicht mehr
  fragen“, ein „erlauben“ pro Tool wird gedeckelt), und die Frage nennt jede Startadresse. Abgelehnt, solange du den
  einmaligen Deep-Ingest-Hinweis nicht gelesen hast (`needs_person`), im Offline-Modus oder bei ausgeschalteter
  Wissensbasis; 1-5 öffentliche `http(s)`-Startadressen, vor der Frage geprüft; `max_pages` bis zur Einstellung und
  100, `max_depth` bis zur Einstellung und 3, mindestens eine Sekunde zwischen Anfragen; ein Crawl gleichzeitig je
  Benutzer, 5 am Tag und 300 gespeicherte Seiten am Tag je Client (Budget und Offline-Modus werden beim Start eines
  freigegebenen Crawls erneut geprüft). `cancel_job` hält nach der aktuellen Seite an und
  behält, was gespeichert ist; das Ergebnis enthält Zahlen und die übersprungenen oder fehlgeschlagenen Adressen mit
  Gründen, nie Seitentext oder einen Ordner dieses Computers. Neu: `llm-cli tools crawl-notice` zeigt den Hinweis in einem Terminal und hält fest, dass
  du ihn gelesen hast.
- **Der Community-Zoo für KI-Clients** (MCP-Tools `list_packs`, `add_pack_from_index`, `export_pack`): Ein Client
  listet den signierten Index und die installierten Packs (Details, Prüfung; `refresh` einmal pro Stunde), fügt ein
  Wissens-, Target- oder Rezept-Pack aus dem signierten Index hinzu (Vertrauensstufe verified oder community, nie
  zurückgezogen, nie aus einer Datei), nachdem du die in der Quarantäne geprüfte Zusammenfassung gesehen hast, bei
  einem Target-Pack mit jedem Image, das seine Dockerfiles beziehen (`FROM`, `COPY --from=`, `RUN --mount=...,from=`),
  klar benannt (Registry, Digest oder „not pinned“, Docker Official Image
  oder andere Registry) und dass sein Image-Build mit Netzzugang läuft, und exportiert ein Pack in eine lokale Datei,
  mit deinem Schlüssel signiert (immer gefragt; ohne vorhandenen Schlüssel abgelehnt; liefert Zahlen der Bereinigung,
  nie den entfernten Text). Eine Kopie in der Quarantäne wird für dasselbe Pack und denselben Hash wiederverwendet
  und bei einem Nein, einer abgelehnten oder abgelaufenen Freigabe oder nach 20 Minuten entfernt. „Ja, und nicht mehr
  fragen“ gilt für eine Pack-Art, Vertrauensstufe und einen Index-Schlüssel (Wissens- und Rezept-Packs,
  `verified`-Target-Packs); darunter installierte Packs sind mit `installed_via` markiert; eine Installation darunter muss die größere
  Speicherreserve (20 GiB oder 10 %) frei lassen, sonst wird gefragt. Kein Tool veröffentlicht
  einen Pull Request.
- **Korrekturvorschläge von KI-Clients** (MCP-Tool `propose_healing_fix`): Ein Client, der einen gescheiterten Build
  analysiert, übergibt dem Framework Zusammenfassung, Ursache, manuellen Schritt und Sicherheit. Der Vorschlag
  durchläuft dieselben Prüfungen wie Dittos eigene Analyse, einschließlich des Filters für gefährliche Befehle, wird
  wie jede lokale Änderung von dir bestätigt, nur für einen gescheiterten Build angenommen (höchstens 10 je Build) und
  als KI-erzeugt mit dem, der ihn vorgeschlagen hat (Aufrufer und selbst angegebener Name des Clients), in
  `get_build_status`, `llm-cli build status <Request>` und der GUI (**Tools > Fix Proposals from AI Clients...**)
  angezeigt. Das Framework führt ihn nie aus, und `resolve_healing` lehnt ab, ihn anzuwenden. Der Prompt
  `diagnose_build_failure` führt zu dem Tool.
- **Hugging Face und lokale Dateien für KI-Clients** (MCP-Tools `search_hf_models`, `get_hf_model_info`,
  `download_hf_file`, `list_local_files`): Die Suche filtert zugangsbeschränkte Modelle standardmäßig mit
  `gated: free` (`any`, `gated`) und sagt, was sie weggelassen hat; die Modelldetails zeigen die Lizenz (freizügig nur
  auf einer Positivliste; `other` mit Name und Link; Lizenz und Zugangsbeschränkung des Basismodells), die Art der
  Zugangsbeschränkung, ob dein gespeichertes Token das Repository lesen kann (im Framework geprüft), Parameter,
  Commit, Dateien und Größe; `download_hf_file` lädt eine Datei als Job nach `models/hf/<owner>--<name>/<commit>/`,
  auf den Commit festgelegt und gegen das sha256 des Hubs geprüft, bevor sie ihren Namen bekommt, fortsetzbar, von
  `cancel_job` angehalten (der Teil bleibt), ohne Größengrenze, aber mit Plattenprüfung; `list_local_files` listet die
  Modell- und Datensatzdateien in den Ordnern, die die KI nutzen darf, über ID und Label. KI-Aufrufe senden nur das
  gespeicherte Token oder gar keins (nie `HF_TOKEN` oder das Token des Hugging-Face-Home-Ordners), und nur an
  huggingface.co; das Token erscheint nie in Eingaben, Ergebnissen oder dem Audit-Log. Der Prompt
  `convert_model_for_board` führt zu diesen Tools.
- **Reranking der Ergebnisse der Wissensbasis**: Ein lokaler mehrsprachiger Cross-Encoder (FastEmbed/ONNX, etwa
  120 MB, beim ersten Gebrauch heruntergeladen, nie im Offline-Modus) liest die Frage zusammen mit jedem
  Kandidaten und verwirft die unter einer Relevanzschwelle, sodass Ditto lieber keinen Auszug bekommt als
  unpassende. Build-Empfehlungen nutzen ihn. Einstellungen: `rag_reranker` (`auto`: ab 6 GB RAM, das größere
  bge-reranker-v2-m3 mit CUDA und 16 GB; `off`; oder eine Modell-ID), `rag_rerank_min_score` (ohne Wert: die
  kalibrierte Schwelle des Modells; 0: keine) und `rag_rerank_default` (auch Chat- und MCP-Suchen reranken; aus).
  Ein Reranker, der nicht laufen kann, wird protokolliert, und Suchen behalten ihre Reihenfolge bis zum nächsten
  Start oder einer Änderung von `rag_reranker`. `llm-cli ditto eval --kind retrieval --rag-variant rerank`
  bewertet ihn: bei den 34 Fragen hit@1 93 % und MRR 0,97 (hybride Suche: 87 % und 0,93), und alle 4 themenfremden
  Fragen bekommen keinen Auszug (Schwelle 0,2: relevante Auszüge erreichten 0,80 oder mehr, themenfremde 0,009
  oder weniger). Er braucht etwa 1 s für 12 Kandidaten und 2–2,6 s für 20 auf einer geteilten CPU mit 4 Kernen und
  brachte weniger als die für den Chat geforderten 0,05 MRR, deshalb bleibt der Chat standardmäßig ohne ihn. Die
  Analyse von Build-Logs und Modulvorschläge behalten die hybride Reihenfolge: Bei den letzten Zeilen
  fehlgeschlagener Builds fand der Reranker den relevanten Auszug in 2 von 6 Fällen unter den ersten 3 (hybride
  Suche: 5 von 6) und schob ihn in einem Fall unter die Schwelle, und beim Suchbegriff für Module verwarf er jeden
  Auszug.
- **Die Stichwortsuche versteht Bezeichner**: `-DGGML_CUDA=ON`, `rknn_toolkit2` oder `rknnToolkitLite` werden
  auch über ihre Teile gefunden (`GGML_CUDA`, `rknn toolkit2`), und Suchen aus einem oder zwei Wörtern wie `E2041`
  oder `GGML_CUDA` nutzen den Stichwortindex. Bezeichner-Fragen des Retrieval-Satzes: hit@3 von 83 % auf 100 %,
  der Rest unverändert.
- **Große Codeblöcke und Tabellen**, die über den Kontext des Embedding-Modells hinausgehen (512 Tokens beim
  Standardmodell), werden an Zeilengrenzen geteilt, jedes Stück mit dem Tabellenkopf oder dem Code-Zaun, sodass
  ihre hinteren Teile überhaupt gefunden werden (Antworten tief in solchen Blöcken: hit@1 von 50 % auf 100 %).
  Kleinere Blöcke bleiben ganz. Importiere ein Dokument erneut, damit es wirkt.
- Modelle der Wissensbasis werden nach 10 Minuten ohne Nutzung entladen und bei der nächsten Suche wieder geladen
  (Einstellung `rag_model_idle_minutes`, 0 hält sie geladen, höchstens 7 Tage). Die Zeit läuft ab dem Ende der
  letzten Suche oder des letzten Imports, nie währenddessen.
- **Verwaltetes lokales Modell** für Dittos Offline-Modus: `llm-cli ditto runtime install` baut `llama-server` auf
  deinem Rechner aus dem gepinnten llama.cpp-Commit, zuerst den CPU-Build (ein paar Minuten, danach funktioniert
  `runtime start`) und dann den GPU-Build, den das Bau-Rezept für Betriebssystem, Architektur und C-Bibliothek des
  Rechners wählt (in dieser Version Vulkan unter Linux; CUDA-, ROCm-, Windows- und macOS-Builds folgen), jeder auf
  diesem Rechner geprüft, bevor er genutzt wird; es baut nativ mit deinem Compiler, CMake und Ninja oder im
  Builder-Container des Frameworks, installiert nie eine Toolchain für dich und nennt die Auswege, wenn beides nicht
  geht. `llm-cli ditto runtime start|stop|status` betreibt den Server auf 127.0.0.1 (freier RAM und VRAM werden bei
  jedem Start neu gelesen; API-Schlüssel je Start in einer privaten Datei; eine geleerte Umgebung, die nur die
  Variablen behält, die GPUs auswählen oder ihre Treiber finden, sodass keine `LLAMA_*`-Option und keine
  `GGML_BACKEND_PATH`-Bibliothek den Server erreicht); `status` zeigt, ob der genutzte Build selbst gebaut,
  importiert oder „prebuilt, not self-built“ ist, seine Build-ID, seinen Commit und seine Prüfung, und ob ein neuer
  Build nötig oder empfohlen ist. Das offizielle vorgebaute Archiv von upstream ist nur die ausdrückliche Wahl
  `runtime install --prebuilt`. `llm-cli ditto models list|download`
  lädt Qwen3.5-Modelle in gepinnten Revisionen in den Ordner `ditto/` des eingestellten Modellordners
  (`models_dir`, den Ditto nutzt; die Laufzeit nimmt ihn auch, wenn sie keinen Ordner bekommt) und prüft sie
  anhand von Größe und sha256. Die Modellstufen reichen bis zu 2-Bit-Modellen für schwache Hardware (3 GB RAM).
  Der Anbieter „local“ folgt dem laufenden Server ohne Konfiguration.
- **Ditto-Evaluation** (`llm-cli ditto eval`): feste Fälle (Analyse von Build-Logs, Build-Empfehlungen,
  Modulvorschläge, Chat mit Tools, Prompt-Injection), nach Regeln bewertet, gegen den konfigurierten Anbieter oder
  je lokaler Modellstufe (`--tier small --tier small-q2`; die Server, die der Lauf startet, werden danach gestoppt,
  einer, den du selbst gestartet hast, läuft weiter, wenn er die Stufe bedient). Es wird nichts gebaut; `--json`
  schreibt den Bericht.
- **Retrieval-Evaluation** (`llm-cli ditto eval --kind retrieval`): 42 deutsche und englische Suchen
  (sprachübergreifende Fragen, Bezeichner wie `GGML_CUDA`, Antworten tief in einer großen Tabelle oder einem
  Skript, die letzten Zeilen fehlgeschlagener Builds und Dittos eigene Suchbegriffe, themenfremde Fragen) gegen
  einen Korpus aus 30 Auszügen aus Framework- und SDK-Dokumentation (`evals/retrieval/`). Der Korpus wird mit dem
  konfigurierten lokalen Embedding-Modell in eine temporäre Wissensbasis indexiert: Es wird kein KI-Modell
  gebraucht, und deine eigene Wissensbasis bleibt unberührt. Der Bericht nennt hit@1, hit@3, MRR, wie oft eine
  themenfremde Frage keinen Auszug bekommt, Werte je Tag und die Suchdauer; er funktioniert offline, sobald das
  Embedding-Modell heruntergeladen ist. Ein manueller und nächtlicher CI-Workflow führt ihn mit
  zwischengespeicherten Modellen aus. Ausgangswerte mit dem Standard-Embedding-Modell für die CPU bei den 34
  Fragen: hit@1 83 %, hit@3 97 %, MRR 0,91; alle 4 themenfremden Fragen bekommen noch Auszüge.
- **Format der Hardware-Profile, Schema 2** (`configs/hardware_probe_schema.yml`, Parser
  `orchestrator/runtime/profile.py`): eine Liste der Schlüssel, die eine Hardware-Probe schreibt, jeder mit Quelle und
  Normalisierung je Betriebssystem und seiner Prüfung. Sie umfasst das Betriebssystem (ID, Version, Familie, Variante
  wie DietPi, Kernel, Init-System), die Architektur des Userlands, die C-Bibliothek und ihre Version (glibc, musl,
  ...), ARM-Skalarprodukt, FP16-Arithmetik und CPU-Cluster, die 14 x86-Merkmale, für die ggml baut, Compute
  Capability und gfx-Target der GPU, Vulkan, Versionen der Rockchip-NPU-Laufzeit und Werkzeugversionen. Der Parser
  lehnt eine Datei in Schema 2 ab, wenn ein Schlüssel doppelt vorkommt, ein Wert leer, länger als 4096 Zeichen oder
  außerhalb des Schemas ist oder Werte nicht zusammenpassen (ein x86-Merkmal auf ARM, eine Compute Capability bei
  einer AMD-GPU). Dateien der älteren Probe-Skripte (Schema 1) werden umgewandelt: Der Hostname, RAM-Teilenummern
  und andere identifizierende Werte werden verworfen, jedes CPU-Merkmal einer `hardware_probe.ps1`-Datei wird
  `UNKNOWN` (dieses Skript fragte Windows mit falschen Konstanten), und das Ergebnis
  verlangt vor einem Build eine neue Probe. Dazu kommen ein Leistungsschlüssel für Benchmark-Ergebnisse, der flüchtige
  und nur festgehaltene Werte nicht berücksichtigt, und ein Profilspeicher je Benutzer (`~/.llm-framework/hardware/`,
  private Dateien).
- **Hardware-Probe für Boards und PCs** (`scripts/hardware_probe.py`, nur Standardbibliothek, Python 3.8 oder
  neuer): schreibt das Hardware-Profil in Schema 2 unter Linux, Windows und macOS, mit Betriebssystem, C-Bibliothek
  und ihrer Version, ARM-Kernclustern, ARM-Skalarprodukt und FP16-Arithmetik, den x86-Merkmalen, für die ggml baut,
  dem SoC, dem NPU-Gerät und jeder GPU (NVIDIA Compute Capability, AMD gfx-Target, Vulkan). `llm-cli scan` führt sie
  im Prozess aus und speichert das Ergebnis als Profil dieses Rechners (`--script` behält das Ersatzskript, `--json`
  schreibt die JSON-Ansicht); `llm-cli scan --device HOST --user BENUTZER` führt sie per SSH mit `python3 -I` auf
  einem Board aus und weicht auf `hardware_probe.sh` aus, wenn das Board kein Python 3.8 hat. Das Skript kommt über
  den SSH-Kanal, also bleibt auf dem Board nichts zurück, und das Profil wird unter der ID des Boards gespeichert. Für
  Rechner ohne Netzzugang beschreibt die README die Probe von Hand. Das installierte Paket enthält eine byte-gleiche
  Kopie der Probe (`orchestrator/runtime/hardware_probe.py`), also funktioniert die Hardware-Erkennung des Frameworks
  auch ohne den Ordner `scripts/`.
- **Build-Rezept für selbst gebautes llama.cpp** (`orchestrator/runtime/build/recipe.py`): Aus dem Hardware-Profil
  des Rechners, der die Programme ausführen wird, wird ein Build-Plan (CMake-Optionen, Ziele, Toolchain-Datei,
  Builder, Prüfungen) mit einer Build-ID, die sich mit jeder Option und jeder Werkzeugversion ändert, aber nicht mit
  flüchtigen Werten wie dem freien Speicher. Jeder Plan baut den festgelegten Commit, schaltet jede Option von
  llama.cpp, die etwas herunterladen kann, aus oder lässt sie ungesetzt (`FETCHCONTENT_FULLY_DISCONNECTED=ON`) und
  baut den Server ohne Unterprozesse. Ein Build auf dem Rechner selbst nutzt `-march=native`,
  Konvertierungswerkzeuge, die in einem Target-Image entstehen, bekommen die CPU-Merkmale des Build-Rechners
  ausgeschrieben, und ein Build für einen anderen Rechner nutzt die CPU-Merkmale der Probe (alle 14 x86-Optionen oder `GGML_CPU_ARM_ARCH`, etwa
  `armv8.2-a+dotprod+fp16` für den RK3566) oder alle CPU-Varianten, wenn die Probe es nicht sagen konnte.
  NVIDIA-GPUs bekommen echten Code für genau ihre Compute Capabilities (jede NVIDIA-GPU muss ihre melden) mit einem
  CUDA-Toolkit, das nicht neuer ist, als der Treiber unterstützt, auf Systemen mit glibc; AMD-GPUs ihr gfx-Target
  (ROCm unter Linux, auf Wunsch); Vulkan und Metal sind Schalter; Windows-Builds nutzen Clang. Der Plan folgt
  Betriebssystem und C-Bibliothek des Ziels: eine Builder-Basis, deren C-Bibliothek (glibc, oder musl unter Alpine)
  nicht neuer ist als die des Geräts, ein Build für einen anderen Rechner braucht also die `LIBC_VERSION`, die seine
  Probe meldet, und eine statisch gelinkte C++-Laufzeit für Geräte-Builds. Ein Linux-Build mit Clang nutzt OpenMP nur,
  wenn libomp installiert ist, und die Build-ID unterscheidet beide; alle CPU-Varianten für ein ARM-Board, dessen
  Merkmale die Probe nicht erkennen konnte, brauchen GCC 14 oder Clang 18 (die Liste von upstream enthält
  `armv9.2-a+sme`), sonst verlangt der Plan eine neue Probe.
- **Selbst gebautes llama.cpp: Bauen, Prüfen, Ablegen** (`orchestrator/runtime/build/`): Ein Plan wird nativ unter
  Linux gebaut (mit dem erkannten Compiler, CMake 3.15 oder neuer und Ninja, in einer geleerten Umgebung) oder im
  Builder-Container des Frameworks, einem Image, das auf deinem Rechner aus `builder.Dockerfile` und einer per Digest
  festgelegten Basis gebaut wird und dessen Schritte Konfigurieren und Bauen ohne Netz laufen, mit schreibgeschützter
  Quelle und der Sandbox der Konvertierungsschritte; beschreibbar sind nur die eigenen Ordner des Builds, und das
  Framework folgt danach nie einem Link aus ihnen hinaus. Das Framework teilt dieses Rezept, nie ein fertig gebautes
  Image.
  Die parallelen Jobs richten sich nach dem verfügbaren Speicher. Sagt die Ausgabe des Konfigurierens, dass etwas
  geholt oder heruntergeladen wurde, scheitert der Build, ebenso wenn sie für einen mit OpenMP geplanten Build keine
  OpenMP-Laufzeit gefunden hat. Bevor ein Build abgelegt wird, wird er geprüft: ELF-Architektur und Bibliotheken
  (Geräte-Builds: keine neuere glibc-Symbolversion als auf dem Gerät, keine geteilte C++-Laufzeit), der festgelegte
  Build und Commit in `--version`, die geplante GPU in `llama-bench --list-devices`, und `llama-server` muss
  `--tools` ablehnen. Rauchtest und kurzer Benchmark auf einem winzigen festgelegten Modell werden mit einem Hinweis
  übersprungen, bis der Katalog eines festlegt; ein anderes Modell wird dort nie geladen. Programme kommen nur aus
  dem eigenen Ordner des Builds, nie aus `PATH`, und jedes Programm, das das Framework startet, bekommt dieselbe
  Liste verbotener Optionen (auch in der Schreibweise `--a_b`, die llama.cpp als `--a-b` liest, und unter jedem
  Namen der Download-Optionen, etwa `-hfr`, `-dr` oder `--mmproj-url`) und eine geleerte Umgebung ohne
  `LLAMA_*`-Variablen. Builds liegen unter
  `~/.llm-framework/runtime/llama.cpp/builds/<Build-ID>/` mit `build.json` und `manifest.json` (sha256 jeder Datei,
  Dauer, Prüfung) und werden wiederverwendet, nie neu gestempelt; nur ein auf diesem Rechner geprüfter Build kann
  aktiv werden, und die Aufbewahrung entfernt nie den aktiven Build, den CPU-Ersatz, den Build, aus dem ein
  laufender Server gestartet wurde, oder den Build eines Boards (Dittos Builds und die Builds der Boards liegen in
  diesem einen Speicher). Ein Build von einem deiner anderen Rechner kann importiert werden: Seine Dateien
  werden gegen das Manifest geprüft, seine Build-ID gegen seinen Bauplan und sein Commit gegen den Pin, und du
  bestätigst, bevor eines seiner Programme läuft; danach muss jedes seiner Programme die Version des Pins melden,
  nicht die, die das Archiv erwartet. `runtime install` von Ditto baut darüber. Ein CPU-Build von `llama-server` und
  `llama-bench` am Pin dauerte auf einer virtuellen Maschine mit 4 Kernen 189 s und ergab 28 MB Programme; Dittos
  CPU-Build mit zusätzlich `llama-cli` und `llama-perplexity` dauerte 195 s.
- **Builds von Dittos Server wechseln den Rechner**: `llm-cli ditto runtime export DATEI` schreibt den genutzten
  selbst gebauten Build, und `llm-cli ditto runtime import DATEI` auf einem anderen Rechner derselben Art prüft ihn
  gegen den Pin und sein Manifest, fragt, bevor eines seiner Programme läuft, prüft ihn dort und macht ihn zum aktiven
  Build. Ein mit `-march=native` gemachter Build wird abgelehnt, bevor irgendetwas läuft, auf einer CPU eines anderen
  Herstellers oder ohne eine der CPU-Funktionen der CPU, die ihn gebaut hat (er bräche bei der ersten Rechnung mit
  SIGILL ab); `runtime start` und die Perplexity-Prüfung überspringen einen solchen Build ebenso, etwa in einem
  Benutzerordner, den sich mehrere Rechner teilen. `llm-cli ditto runtime prune` entfernt alte Builds und behält immer
  den aktiven Build, den CPU-Ersatz und den Build, aus dem ein laufender Server gestartet wurde.
- `llm-cli doctor` prüft die lokale llama.cpp-Laufzeit: einen genutzten selbst gebauten Build, der verändert wurde
  (Fehler) oder älter als der Pin ist (Warnung), das genutzte vorgefertigte Archiv (Information: nicht selbst gebaut)
  und ob dieser Rechner Compiler, CMake und Ninja für einen nativen Build hat (Information, mit dem Builder-Container
  als Alternative).
- **llama.cpp per Commit festgelegt**: `llama_cpp.source` in `configs/llama_runtime.yml` (Commit
  `d81235049384534c167caea52b85a694f6103d14`, Tag `b11429`) ist die eine Stelle. Dazu kommen die CUDA-Toolkits für
  selbst gebaute CUDA-Builds (die Redist-Manifeste von NVIDIA per Größe und SHA256 festgelegt, mit den Grenzen für
  Treiber, Compute Capability, Host-Compiler und CMake je Toolkit; die vorgebauten Entwicklungs-Images von NVIDIA nur
  als ausdrücklicher Opt-in-Eintrag) und die Builder-Basen, per Digest festgelegt. Ein Test schlägt fehl, wenn eine
  andere Stelle, die die llama.cpp-Version nennt, abweicht, auch das `ARG LLAMA_CPP_COMMIT`, das jedes Target-Dockerfile
  braucht, das llama.cpp baut, und `python scripts/llama_cpp_pin.py check` prüft mit
  `git ls-remote`, dass der Tag bei Upstream auf den festgelegten Commit zeigt.
- **Festgelegter llama.cpp-Quelltext für Rechner ohne Netz**: `llm-cli ditto runtime export-source DATEI` schreibt
  den festgelegten Commit als Git-Bundle mit Manifest, und `llm-cli ditto runtime import-source DATEI` installiert ihn
  auf einem Rechner ohne Internetzugang. Der Quelltext wird per Commit geholt (über seinen Tag nur, wo ein Server das
  ablehnt), muss den festgelegten Commit zurückgeben und liegt schreibgeschützt in
  `~/.llm-framework/runtime/llama.cpp/src/`; spätere Läufe prüfen, dass darin nichts geändert oder hinzugefügt wurde,
  auch keine Dateien, die Git ignoriert, und brauchen kein Netz.
- **Konvertierungswerkzeuge abgestimmt auf den Build-Rechner**: Target-Images bauen die Konvertierungswerkzeuge von
  llama.cpp (`llama-quantize`, `llama-imatrix`, `llama-perplexity`, `llama-completion` und jetzt `llama-bench`) nach
  dem Build-Rezept. Der Image-Build bekommt den Pin (`LLAMA_CPP_TAG`, `LLAMA_CPP_COMMIT`) und den Plan für den Zweck
  `host-tools` aus einer Probe dieses Rechners unmittelbar davor (`LLAMA_CMAKE_ARGS`: seine CPU-Merkmale
  ausgeschrieben, oder alle CPU-Varianten, wenn die Probe es nicht sagen konnte). Das Rockchip-Dockerfile und die
  Vorlage holen den Commit und brechen ab, wenn sie nicht genau diesen bekommen, lehnen Wörter ab, die nicht
  `-DNAME=WERT` sind, und Optionen, die bei jedem Setzen etwas herunterladen, setzen nach dem Plan die Optionen, die
  jeder Build hat (Download-Optionen aus, `LLAMA_SUBPROCESS=OFF`, `FETCHCONTENT_FULLY_DISCONNECTED=ON`, Build-Nummer
  und Commit), brechen bei einem Konfigurationsprotokoll ab, das einen Download meldet, und prüfen
  `llama-bench --version`. Ohne Probe, oder mit einem Docker-Daemon auf einem anderen Rechner, gilt die Vorgabe des
  Dockerfiles: die feste x86-64-Basis mit AVX2, die diese Werkzeuge immer hatten, ausgeschrieben und jetzt statisch
  gelinkt. `build_info.json` hält die Abstimmung fest (`llama_cpp_tools`). Zehn Quantisierungstypen eines Testmodells
  waren aus der Basis, einem Build mit AVX-512/AMX und einem ohne AVX2 byteweise gleich; der Bau der fünf Werkzeuge
  dauerte auf einer virtuellen Maschine mit 4 Kernen 3 bis 6 Minuten.
- **Block `runtime:` in neuen Target-Modulen**: Der Modul-Assistent, `llm-cli module create` (neue Option
  `--probe DATEI`) und `llm-cli module generate-ai` schreiben den Plan des Build-Rezepts für die llama.cpp-Laufzeit des
  Boards aus der Probe des Targets in `target.yml`: Architektur, Betriebssystem, die C-Bibliothek, die der Cross-Build
  nicht überschreiten darf, Builder-Basis, CPU-Optionen (`armv8.2-a+dotprod+fp16` für den RK3566), Backends und
  Programme, aus dem Quelltext gebaut. Der Block ist eine Aufzeichnung (eine Geräte-Laufzeit wird immer neu aus der
  Probe des Geräts geplant) und wird in Paketen gegen sein Schema geprüft. CPU- und CMake-Flags aus dem Assistenten
  oder von Ditto stehen dort als Hinweistext und werden nie angewendet; der Assistent kennzeichnet sie so.
- **Selbst gebautes llama.cpp auf dem Board** (`llm-cli device runtime plan|build|deploy|verify|status`,
  `llm-cli device list|settings`): Das eigene Probe-Profil des Boards (`llm-cli scan --device`) wird zu einem
  Cross-Build im Builder-Container, ohne Netz, auf einer Basis, deren C-Bibliothek nicht neuer ist als die des Boards
  (glibc, oder musl unter Alpine; ein Board, das älter ist als jede Basis, bekommt eine klare Meldung), mit statisch
  gelinkter C++-Laufzeit und bei OpenMP-Builds der `libgomp.so.1` des Boards aus der Cross-Toolchain. Das Ergebnis
  ist ein Tarball (`llama-server`, `llama-cli`, `llama-bench`, `llama-perplexity`, `lib/libgomp.so.1`, ein
  Startskript, die Stempel des Builds, die MIT-Lizenz von llama.cpp und `SHA256SUMS`), den das Deployment per SSH
  nach `~/.local/share/llm-framework/llama.cpp/` auf dem Board hochlädt und dort Datei für Datei prüft. Dann werden
  die Programme des neuen Builds auf dem Board als dein unprivilegierter Benutzer geprüft, bevor irgendetwas sie
  startet: `--version` gegen den Pin, `--list-devices`, die Ablehnung von `--tools` durch den Server und mit
  `--bench-model` ein kurzer `llama-bench`-Lauf (ein Modell, das kein Katalogmodell ist, erst nach deiner
  Zustimmung, die ins Audit-Log geschrieben wird). Nur ein Build, der besteht, wird über einen Link `current` aktiv
  (der vorige Build bleibt); ein Build, der scheitert, etwa mit `SIGILL`, weil die CPU-Grundstufe nicht zum Board
  passt, wird wieder entfernt, und der vorige Build bleibt aktiv. Das Startskript startet den Server auf 127.0.0.1
  mit einem Schlüssel, der auf dem Board unter umask 077 erzeugt wird, einer geleerten Umgebung und festen Optionen;
  es reicht nur wenige Tuning-Optionen mit geprüften Werten weiter (Kontextgröße, Threads, CPU-Maske, ...). Wie die
  Laufzeit startet, wird vorher in den Deploy-Einstellungen festgelegt und während eines Deployments nie gefragt: das
  Startskript in deinem Home (die Vorgabe) oder eine systemd-Unit, die nur für Boards mit systemd wählbar ist und
  erst, nachdem du bestätigst, dass Deployments dort `sudo -n` ausführen dürfen. Getestet mit einem
  nachgebildeten SSH-Kanal und Testprofilen (ein RK3566 unter DietPi, ein Alpine-Board, ein Board ohne systemd) und
  mit echten Programmen: `llama-server` und `llama-bench`, nativ mit den Optionen des Geräte-Bauplans für einen
  x86-64-Rechner gebaut (30 MB, ein Tarball von 11,6 MB), bestanden die Formatprüfung, das Deployment auf ein lokal
  nachgebildetes Board und die Prüfungen dort. Noch nicht auf einem echten Board, und hier lief kein Cross-Build
  (kein Docker-Dienst).
- **Deep Ingest beachtet maschinenlesbare Nutzungsvorbehalte.** Text und Data Mining öffentlicher Seiten ist
  erlaubt, solange der Rechteinhaber es nicht maschinenlesbar vorbehalten hat (EU-DSM-Richtlinie Art. 4,
  § 44b UrhG). Deshalb überspringt der Crawler neben robots.txt Seiten, die in einem `X-Robots-Tag`-Header oder in
  `<meta name="robots">` / `<meta name="llm-conversion-framework-bot">` mit `noindex`, `none`, `noai` oder
  `noimageai` markiert sind, folgt den Links von `nofollow`-Seiten nicht und beachtet das W3C TDM Reservation
  Protocol (`/.well-known/tdmrep.json` je Site, Header und Meta-Tag `tdm-reservation`, PDF-Metadaten).
  Weiterleitungsziele auf anderen Hosts werden genauso geprüft. Jede Seite, die nicht gespeichert wird, steht mit
  ihrem Grund im Crawl-Protokoll. Keine dieser Prüfungen lässt sich abschalten.
- **Hinweis vor dem ersten Deep Ingest** (Englisch und Deutsch): was gelesen wird (nur öffentlich erreichbare
  Seiten), welche Vorbehalte beachtet werden, dass das Wissen auf diesem Computer bleibt und dass beim Weitergeben
  Quelle und Lizenz erhalten bleiben und deine ausdrückliche Freigabe nötig ist. Er ist eine Information, keine
  rechtliche Bestätigung: ein Knopf „Verstanden“. Er erscheint einmal je Benutzer und erneut, wenn sich sein Text
  ändert.
- **Gesprochene Ansagen** des Hinweises und des Ergebnisses von Deep Ingest über die Sprachausgabe des
  Betriebssystems (Windows System.Speech, macOS `say`, Linux `spd-say` oder `espeak-ng`), in der Sprache der
  Oberfläche. Nach bestem Bemühen und nie im Weg: keine Audiodateien, die Systemlautstärke wird nie verändert, und
  still in CI, über SSH ohne Bildschirm, auf Systemen ohne Bildschirm, mit `LLM_FRAMEWORK_NO_AUDIO=1` oder wenn
  *Werkzeuge > Gesprochene Ansagen* (Einstellung `audio_announcements`) aus ist. Jede Ansage wird auch
  protokolliert. Der Schalter warnt, wenn er die Einstellung nicht speichern kann.
- **Fortschritt und Stopp bei Deep Ingest**: Der Assistent zeigt „Seite N von M: <url>“ statt eines endlosen
  Drehsymbols, und ein Stopp-Knopf beendet den Crawl nach der aktuellen Seite. Das Schließen des Assistenten
  stoppt einen laufenden Crawl genauso und schließt, sobald die aktuelle Seite gespeichert ist, ohne dass das
  Fenster einfriert.
- **Eingabeverlauf**: Die Eingabefelder bieten an, was du zuletzt verwendet hast, das Neueste zuerst. Modell,
  Kalibrierungsdatensatz, Deep-Ingest-URL und Gerät (Deployment, Hardware-Scan per SSH) sind bearbeitbare Listen;
  das letzte Modell und das letzte Gerät werden eingetragen, Datensätze und URLs nur angeboten. Targets und Boards
  kommen aus den Target-Definitionen, deshalb wird das zuletzt gebaute vorausgewählt, sofern es noch angeboten
  wird (ein Target ohne angegebene Boards bietet die letzten Boards als Vorschläge an). Der Verlauf gilt je
  Benutzer (`input_history.json` im Zustandsordner des Benutzers), hat höchstens 20 Einträge je Feld und enthält
  nie Secrets: Werte, die wie ein API-Schlüssel oder Token aussehen, URLs mit Benutzername oder Passwort und URLs
  mit einem Zugangs- oder Sitzungsparameter (etwa `?token=` oder `;jsessionid=`) werden nicht gemerkt.
  *Werkzeuge > Eingabeverlauf löschen...* vergisst ihn.
- **Signierte Community-Packs**: Ein Pack ist ein Zip mit einem Manifest (sha256, Größe und Rechte jeder Datei)
  und einer optionalen Ed25519-Signatur des Autors; der öffentliche Schlüssel des Autors wird bei der ersten
  Installation festgehalten, und ein anderer Schlüssel unter demselben Autorennamen wird mit einer Warnung
  gezeigt. Beim Lesen eines Packs werden genau die aufgeführten Dateien angenommen, nie Links oder Sonderdateien,
  und jeder Hash wird geprüft. Vertrauenswürdige Herausgeberschlüssel werden in `configs/community_keys.yml`
  mitgeliefert, jeder mit einem Ablaufdatum.
- **Verschlüsselte Packs zum privaten Teilen** (mit deinen eigenen Rechnern oder einem Team): im Format age von
  rage verschlüsselt (über `pyrage`), für die age-Schlüssel eines oder mehrerer Empfänger oder für eine
  Passphrase, und vom Absender signiert, sodass Empfänger sehen, wer es geschickt hat, und jede Änderung erkannt
  wird, auch ein hinzugefügter oder entfernter Empfänger. Die üblichen Werkzeuge `age` und `rage` öffnen sie
  ebenfalls, wenn sie für eine Passphrase oder für einen Schlüssel aus `age-keygen` oder `rage-keygen`
  verschlüsselt sind. Ein Pack mit Passphrase, dessen scrypt-Arbeitsfaktor mehr als 1 GiB Speicher braucht, wird
  abgelehnt, bevor irgendetwas entschlüsselt wird. Jeder Benutzer bekommt beim ersten Gebrauch einen
  Ed25519-Schlüssel und eine age-Identität, die nur im verschlüsselten Secrets-Speicher liegen; die öffentlichen
  Schlüssel lassen sich als Text exportieren.
- **Rezepte**, die „Modellvorlagen“ des Model Zoo: das Modell in einem gepinnten Commit, das Target mit einem
  Fingerabdruck seiner Dateien, Board, Format, Quantisierung, Optionen und geprüfte Ergebnisse (Hashes der
  Artefakte, Toolchain-Image, Gerätetests). Ein Rezept entsteht aus einem fertigen Build, wird gegen die
  Capabilities des Targets geprüft und ergibt eine normale Build-Anfrage.
- `build_info.json` hält fest, was ein Rezept braucht: den Commit des Modells (der Download wird darauf gepinnt),
  Fingerabdruck und Version des Targets, das Toolchain-Image, die Kontextlänge, den Hash des
  Kalibrierungsdatensatzes und die Version des Frameworks. Build-Anfragen nehmen für Hugging-Face-Modelle eine
  `revision` (Commit) an.
- **Community Zoo** (`llm-cli zoo list|show|install|verify|export|keys`): Community-Targets, Rezepte und
  Wissenspacks aus einem signierten Community-Index (Einstellung `community_index_url`: `index.json` plus
  `index.json.sig`, geprüft mit den Schlüsseln in `configs/community_keys.yml`, je Benutzer zwischengespeichert,
  neu abgerufen, wenn der Zwischenspeicher älter als einen Tag ist, und nie durch einen älteren Index ersetzt).
  Eine Installation liest immer zuerst den aktuellen Index (so wird kein Widerruf verpasst), lädt das Pack
  gepinnt nach Größe und sha256 herunter, prüft es in einem Quarantäne-Ordner (genaue Dateiliste und Hashes, die
  Target-Prüfungen, die Schemas für Rezepte und Wissen), zeigt eine Zusammenfassung mit seiner Vertrauensstufe und
  installiert es erst nach deiner Bestätigung, atomar und mit einer Herkunftsdatei. Vertrauensstufen: verified,
  community, local (eine Datei ohne Indexeintrag: immer mit Bestätigung und mit einer Warnung bei jedem Build) und
  revoked (gesperrt; installierte Kopien werden von `llm-cli zoo list`, `zoo verify`, `llm-cli doctor` und dem Hub
  markiert und nicht gebaut, auch nachdem der Schlüssel, der den Widerruf signiert hat, abgelaufen ist).
  `llm-cli zoo verify` findet installierte Packs, die verändert oder widerrufen wurden. Die Zusammenfassung vor der
  Installation wird immer als reiner Text gezeigt, sodass Text aus einem Pack seine Vertrauensstufe nicht verdecken
  kann. Ein Wissenspack wird bei seiner Installation in die Wissensbasis aufgenommen oder, wenn die Wissensbasis
  erst später eingeschaltet wird, dann.
- **Community Hub** im Menü Werkzeuge: eine Spalte für die Vertrauensstufe, Filter nach Art und Vertrauen, Import
  von Pack-Dateien (auch von Packs, die für deinen Schlüssel verschlüsselt sind), und „Build“ bei einem
  installierten Rezept füllt den Builder aus, einschließlich des gepinnten Commits des Modells.
- **Export für die Community** auf der letzten Seite des Modul-Assistenten, im Kontextmenü des Builder-Protokolls
  (ein fertiger Build wird zum Rezept) und mit `llm-cli zoo export`: ein Pack eines Targets, eines Rezepts oder
  deiner Wissensbasis, von persönlichen Daten bereinigt (die Änderungen werden gezeigt), geprüft und mit deinem
  Schlüssel signiert; mit `--to` oder `--passphrase` wird es stattdessen zum privaten Teilen verschlüsselt.
  Wissenspacks behalten die Quelladresse jeder Seite und beanspruchen keine Lizenz für sie (`NOASSERTION`); ein
  Community-Pack, das du installiert hast, wird nicht als dein eigenes neu signiert, und Seiten aus installierten
  Wissenspacks bleiben aus einem Wissens-Export draußen. „Open pull request“ (`--pr`,
  das nach der Zusammenfassung nachfragt, außer mit `--yes`) übergibt es an das Community-Repository (Einstellung
  `community_repo`): über die GitHub CLI, wenn sie installiert und angemeldet ist, sonst öffnet sich die
  Upload-Seite des Repositorys im Browser. Das Framework fragt nie nach einem Token, liest keins und zeigt keins.
- Werkzeuge für die Maintainer des Community-Repositorys: `scripts/zoo_sign_index.py` (Signaturschlüssel im
  Schlüsselbund des Betriebssystems, Indexeinträge aus Packs, Signatur von `index.json`) und die
  CI-Workflow-Vorlage `scripts/community_repo/validate-contribution.yml`, die `llm-cli zoo verify` für jedes
  eingereichte Pack ausführt.
- **Claude-Workspace-ID** (Feld „Workspace ID“ in den KI-Einstellungen, Einstellung `ai_anthropic_workspace_id`):
  Claude-API-Schlüssel, die in jedem Workspace der Organisation gelten, funktionieren jetzt. Die ID wird als
  Header `anthropic-workspace-id` bei jeder Anfrage mitgeschickt, die mit dem API-Schlüssel an die Claude-API
  geht, auch beim Abruf der Modellliste. Ein `ant auth login`-Profil behält seinen eigenen Workspace, und andere
  Server bekommen die ID nie.
- **Anmeldeverfahren je Anbieter** (`configs/llm_providers.yml` Schema 2, `orchestrator/llm/auth`): Jeder
  Anbieter nennt seine Anmeldeverfahren in der Reihenfolge, in der sie bevorzugt werden, mit der Seite, auf der
  der Schlüssel erstellt wird, den Umgebungsvariablen, die er liest, einer kostenlosen Prüfung und zusätzlichen
  Feldern wie der Claude-Workspace-ID oder der OpenAI-Organisation und dem Projekt. Kataloge mit Schema 1 in
  deinem Einstellungsordner werden weiterhin geladen; einer mit einem neueren Schema wird mit einer Meldung
  abgelehnt, statt falsch gelesen zu werden. Angaben zu einer Anmeldung, die nicht geheim sind (Konto
  oder Workspace, die letzten vier Zeichen eines Schlüssels, zusätzliche Felder, Zeitpunkt der Prüfung), liegen
  in `auth.json` neben dem Tresor. Tokens der Anmeldeabläufe, die später kommen, werden unter einer Sperre
  erneuert, die GUI, CLI und MCP-Server teilen; das neue Refresh-Token wird gespeichert, bevor es genutzt wird,
  und eine abgelehnte Erneuerung markiert die Anmeldung als abgelaufen.
- **Prüfung der Zugangsdaten** (die Schaltfläche **Prüfen** im Anmeldebereich und
  `llm-cli ditto auth status --check`): Eine kostenlose GET-Anfrage (die Modellliste, bei OpenRouter der
  Schlüssel-Endpunkt) zeigt, ob der gespeicherte Schlüssel funktioniert, zu welchem Workspace oder Schlüsselnamen
  er gehört und wie viele Modelle er nutzen kann; ein Schlüssel, der funktioniert, aber keine Modelle auflisten darf, wird als solcher gemeldet, auch von
  der Live-Modellliste in den KI-Einstellungen. Weiterleitungen werden nicht verfolgt, der Schlüssel geht also
  nur an den Anbieter.
- **API-Schlüssel aus Umgebungsvariablen**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` und
  `OPENROUTER_API_KEY` werden genutzt, wenn für den Anbieter kein Schlüssel gespeichert ist (nur die Variablen,
  die der Anbieterkatalog nennt; gelesen vom Framework, nie von den SDKs). Die KI-Einstellungen nennen die
  Variable, nichts wird in den Tresor kopiert, und ein gespeicherter Schlüssel hat immer Vorrang. Lässt sich der
  Tresor nicht lesen, springt keine Variable für den Schlüssel ein, der dort gespeichert sein kann: Die
  Fehlermeldung sagt, was mit dem Tresor nicht stimmt.
- **Wie Zugangsdaten geschützt werden, entscheidest du** (Einstellung `secret_storage`, oder
  `LLM_SECRET_STORAGE` für einen einzelnen Lauf): der Schlüsselbund des Betriebssystems (Standard), ein Befehl als
  Credential-Helper (`secret_helper`, z. B. `pass show llmcc/vault-key`, ohne Shell ausgeführt), eine Passphrase,
  die einmal je Start abgefragt wird (scrypt), eine Schlüsseldatei neben dem Tresor (`insecure`, im Ruhezustand
  nicht verschlüsselt) oder nur der Arbeitsspeicher (`ephemeral`, für CI). Nichts wird automatisch gewählt: Ohne
  nutzbaren Schlüsselbund und ohne Wahl hält das Framework an und nennt die Möglichkeiten. `llm-cli doctor` warnt
  vor der Schlüsseldatei und meldet die reine Speicherung im Arbeitsspeicher.
- **`--debug-http`** (`llm-cli --debug-http` und dieselbe Option beim Start der GUI) zeigt die Debug-Ausgabe
  der HTTP-Bibliotheken und der SDKs der KI-Anbieter auf stderr, mit geschwärzten Zugangsdaten.
- **Dokumentation auf Englisch und Deutsch** für jedes Dokument (README, Blaupause mit Design und Roadmap,
  CLI-Anleitung, Funktionen, Mitwirken, Leitfaden für Modulentwickler, SSOT, Changelog), geprüft durch
  `tests/test_docs.py`: Jedes Dokument hat sein Gegenstück, Links und Anker funktionieren, beide Changelogs nennen
  dieselben Versionen, und die Roadmap nennt nur, was kommt.
- **Anmeldung auf der Kommandozeile**: `llm-cli ditto login <anbieter>` meldet Ditto mit den Verfahren des
  Anbieters an (ein API-Schlüssel, eine lokale Engine ohne Schlüssel, ein Profil aus `ant auth login` mit
  `--method ant_profile`), prüft einen Schlüssel kostenlos, bevor es ihn speichert, und merkt sich das Verfahren
  (`ai_auth_<anbieter>`); `--workspace-id`, `--organization`, `--project` und `--base-url` legen fest, was zum
  Schlüssel gehört. `llm-cli ditto logout` löscht die lokale Kopie und nennt die Schlüsselseite, auf der der
  Schlüssel selbst widerrufen wird. `llm-cli ditto auth status [--json] [--check]` zeigt die Anmeldung aller
  Anbieter (Verfahren, Quelle, Konto, die letzten vier Zeichen eines Schlüssels), `auth methods` die Verfahren
  mit Hinweisen zu den Nutzungsbedingungen, `auth storage [--use ...]`, wie der Tresor geschützt ist (eine Wahl,
  die scheitert, ändert nichts, auch nicht `config.yml`), und `auth rotate-key` ersetzt den Schlüssel des
  Tresors. Nach einer geprüften Anmeldung nennt der Status das Konto oder den Workspace. Exit-Codes:
  2 Bedienfehler (auch ein leeres stdin, eine fehlende Serveradresse, ein fehlerhafter Optionswert oder ein
  Schlüssel mit Leerzeichen oder Zeilenumbruch darin), 3 abgelehnt, 4 Netz, 5 Tresor nicht nutzbar, 6 nicht
  erlaubt oder nicht verfügbar.
- **Anmeldebereich in den KI-Einstellungen** (**Anmelden...**): die Verfahren des Anbieters als Karten in der
  Reihenfolge des Katalogs, das erste nutzbare als „Empfohlen“ markiert; ein Schlüsselformular mit
  Schlüsselseite, den zusätzlichen Feldern, **Prüfen**, **Speichern** und **Abmelden**; eine Statuszeile
  („Angemeldet als ...“, „Nicht angemeldet“, „Schlüssel aus der Umgebungsvariable X“); und ohne nutzbaren
  Schlüsselbund die Auswahl für den Schutz, die die GUI auch beim Start zeigt, bevor sie aufgibt (eine Wahl dort,
  die den vorhandenen Tresor nicht öffnen kann, wird nicht behalten, damit der nächste Start noch funktioniert).
  Ein mit Passphrase geschützter Tresor fragt sie in einem Dialog ab. Jeder Zugriff auf Tresor, Schlüsselbund und Netz
  läuft in einem Hintergrund-Thread, und ein älteres Lesen, das spät fertig wird, ersetzt nie einen neueren
  Stand. Der Kopf von Dittos Chat zeigt den genutzten Zugang, zum Beispiel „Lokal · Ollama“. Ein Schlüssel im
  eigenen Schlüsselfeld der KI-Einstellungen ersetzt ein Verfahren, das sich eine Anmeldung gemerkt hat (etwa
  das `ant`-Profil), damit er nicht ungenutzt bleibt.
- `llm-cli secrets set --with-token` liest den Wert von stdin.
- **Cancel Build** im Builder-Tab: bricht den laufenden Build nach einer Bestätigung über den Build-Service ab
  (nur aktiv, solange der Build läuft, den der Tab gestartet hat); das Log meldet, wenn er angehalten hat, auch
  nach einem Abbruch, während der Build noch an die Build-Engine übergeben wurde.
- **Mehr Zusammenhang in Audit-Einträgen**: der Bestätigungsweg (`elicitation`, `framework-gui`, `framework-cli`),
  der SHA-256 des Fragetexts, die Antwort der Person (angenommen, abgelehnt, abgebrochen, nicht möglich,
  unbeantwortet, abgelaufen), der selbst angegebene Name, die Version, der Transport und das Protokoll des Clients
  (nur eine Bezeichnung; ein Schlüssel darin wird vor dem Kürzen entfernt, wie in der Zeile auf stderr), ein Hash
  der Verbindung, die Dauer, SHA-256 und Größe des Ergebnisses (nie das Ergebnis selbst) und ein Verweis vom
  Abschlusseintrag auf den vorab geschriebenen Eintrag des Aufrufs. Die Felder sind an die Hash-Kette gebunden, ohne
  zu ändern, wie 3.0.0 einen Eintrag hasht: Logs, die vor ihnen geschrieben wurden, bleiben prüfbar, und ein noch
  laufender 3.0.0-Prozess (ein MCP-Server, den ein KI-Client vor dem Update gestartet hat) prüft das Log weiter und
  schreibt hinein, statt die neuen Einträge als verändert zu melden.
- **Tool-Berechtigungen pro Aufrufer**: Ditto und lokale MCP-Clients über stdio können eigene Berechtigungen haben
  (`llm-cli tools permissions --caller ditto|mcp`, `--reset --caller` kehrt zu den gemeinsamen zurück); die ersten
  Berechtigungen eines Aufrufers beginnen mit den gemeinsamen, samt Einstellungen für einzelne Tools.
  `llm-cli tools list --caller`, die Einrichtung in der CLI und der GUI-Dialog zeigen die eigene Vorgabe jedes Tools,
  die höchste Entscheidung, die eine Einstellung ihm geben kann, ob es neu ist (noch nicht gesehen) und ob eine
  dauerhafte Freigabe angeboten werden darf.
- **Tool `get_status`** (Ditto und MCP-Clients): die Tool-Berechtigungen des Aufrufers, mit Entscheidung und Grund
  für jedes Tool, auch für die Tools, die nicht aufgeführt werden, weil sie verweigert sind, und die Handlungen, die
  kein Tool übernimmt, mit dem Ort, wo die Person sie erledigt, damit eine KI den echten Grund nennen kann, wenn sie
  etwas nicht tun kann.
- **Abgesicherte Tool-Aufrufe** (Ditto und MCP-Clients, jede Modellgröße): Schreibvarianten von Tool-Namen
  (`StartBuild` startet `start_build`) und angegebene Argument-Aliasse (`quant` für `quantization`) laufen mit
  einem Hinweis, während jeder andere unbekannte Argumentname, auch einer in anderer Groß- und Kleinschreibung, mit
  einem Vorschlag abgelehnt wird; Werte werden auf beiden Wegen gleich umgewandelt und mit Formaten, Boards und Quantisierungstypen
  des Targets abgeglichen, genau, über die kanonische Form, wenn sie eindeutig ist, oder über die Aliasse des
  Targets, nie durch Raten eines knapp danebenliegenden Werts; jede Ablehnung trägt einen Grundcode und den nächsten
  Schritt; derselbe fehlschlagende Aufruf wird beim zweiten Mal gekennzeichnet und beim dritten Mal abgelehnt, ohne
  zu laufen; nach drei Eingabefehlern zeigt ein Fehler einen gültigen Beispielaufruf. Was repariert wurde, ist in
  der Frage markiert, in den `notes` des Ergebnisses aufgeführt und im Audit-Log festgehalten. Tool-Argumente, die
  ein lokales Modell mit Codeblock-Zäunen oder in Python-Schreibweise schickt, werden einmal erneut gelesen.
- **Zweistufige Bestätigung und Rückfragetext v2** (Ditto und MCP-Clients): Eine Rückfrage beginnt mit der Folge in
  einfachen Worten aus Fakten, die das Framework nachgeschlagen hat, dann wer fragt (Name, Version und Transport, die
  ein MCP-Client selbst angibt, und wessen Berechtigungen gelten), die Fakten, die das Framework geprüft hat (externer
  Text als "not checked" gekennzeichnet), und jedes Argument mit dem sprechenden Namen einer ID (Modell und Ziel eines
  Builds, das Label einer lokalen Datei). Ein Tool kann Fakten vor der Rückfrage nachschlagen, und der Aufruf ist an
  sie gebunden: `resolve_healing` zeigt den offenen Vorschlag aus dem eigenen Zustand des Frameworks, lehnt einen
  Vorschlag ab, der sich nach der Rückfrage geändert hat, und fragt vor einem zerstörenden Fix (`docker_prune`) immer,
  auch wenn das Tool erlaubt ist. Dittos GUI-Dialog und CLI-Abfrage zeigen Rückfragen bis zu 16.000 Zeichen (ein
  scrollbarer Dialog, der Pager); ein MCP-Formular bleibt bei 4.000.
- **Grenzen je Client für Tool-Aufrufe** (Ditto und MCP-Clients): lesende Tools 20 Aufrufe in 10 Sekunden,
  `start_build` 3 unfertige Builds und 20 Starts am Tag, 30 Rückfragen pro Stunde und eine Build-Warteschlange von
  höchstens 20 unfertigen Anfragen je Framework-Prozess für KI-Aufrufer (die eigenen GUI- und CLI-Builds des
  Menschen werden immer eingereiht); nach einem Nein wird derselbe Aufruf 10 Minuten lang nicht erneut erfragt, und
  drei Nein zu einem Tool pausieren seine Rückfragen für 30 Minuten. Eine
  erreichte Grenze ist eine protokollierte Ablehnung `rate_limited`, die sagt, wann es wieder geht. Die
  Wiederholungssperre lässt einen abgelehnten Aufruf 10 Minuten nach seinem letzten Scheitern wieder laufen (bisher
  war er bis zum Ende des Prozesses gesperrt), und Ditto hört nur nach Fehlern ohne Fortschritt vorzeitig auf, Tools
  aufzurufen, mit bis zu 4 Schritten mehr, solange er einen Eingabefehler behebt.
- **`llm-cli tools audit` zeigt die neueren Audit-Felder in Worten**: wer aufgerufen hat (Ditto, alle lokalen
  stdio-Clients, ein benanntes Token, mit dem Namen, den der Client selbst angibt), den Titel des Tools und den Namen,
  den die KI geschrieben hat, den Grundcode und wie der Mensch gefragt wurde, mit seiner Antwort; `--details` ergänzt
  Reparaturen, Dauer, Hash des Ergebnisses, Verweise und Sitzung.
- **`llm-cli ditto eval` hält jeden Versuch eines Tool-Aufrufs fest**, nicht nur die, die ein Tool erreicht haben:
  Der Block `tool_use` des Berichts nennt Tool-Aufrufe pro Aufgabe, den Anteil fehlerhafter Aufrufe und unbekannter
  Tool-Namen, Reparaturen und Ablehnungen wiederholter Aufrufe.
- **Ordner, die die KI nutzen darf**: Ditto und MCP-Clients nennen ein lokales Modell oder einen
  Kalibrierungsdatensatz als `<Label>:<relativer Pfad>` (zum Beispiel `models:qwen2.5-0.5b-instruct`,
  `datasets:calib/de.txt`) oder über seine Datei-ID im Modellordner, im neuen Datensatzordner (Einstellung
  `datasets_dir`, standardmäßig neben dem Modellordner) oder in einem Ordner, den du mit
  `llm-cli tools folders add|list|remove` (Terminal nötig) oder in der GUI (Tools > Folders the AI May Use...)
  hinzufügst. Das Hinzufügen zeigt, was das Teilen bedeutet, und wird im Audit-Log festgehalten (ein Hinzufügen,
  das sich nicht festhalten lässt oder dessen Ordner sich während der Frage ändert, findet nicht statt); `/`, dein
  Home, ein Ordner mit dem persönlichen Ordner oder einem gesperrten Ordner darin lassen sich nicht hinzufügen, und
  dieselben Grenzen gelten für die Einstellungen des Modell- und des Datensatzordners. Der Ordner `hub` des
  Hugging-Face-Caches lässt sich hinzufügen; seine Token-Dateien bleiben gesperrt.
- **Jobs und Warten für MCP-Clients und Ditto**: die Tools `get_job_status` und `cancel_job` für lange Operationen
  außer Builds (eine Job-ID `job_...`, ihr Zustand, Fortschritt, Ergebnis oder Fehler; eine Zusammenfassung jedes
  Jobs bleibt in `~/.llm-framework/jobs/`), und `wait_seconds` bei `get_build_status` und `get_job_status`: Der
  Aufruf kehrt zurück, sobald sich der Build oder Job ändert oder endet, spätestens nach 50 Sekunden (ein größerer
  Wert wird mit einem Hinweis verringert), mit Fortschrittsmeldungen für Clients, die sie anfordern. Das Warten
  belegt keinen Thread, je Client laufen höchstens 4 Wartevorgänge, und ein abgebrochenes Warten berührt den Build
  oder Job nie.
- **Der MCP-Server nennt seine Clients:** Für jeden neuen Client schreibt `llm-cli mcp serve` eine Zeile nach
  stderr (nie an den Client) mit Name und Version, die der Client angibt, der Protokollversion, dem Transport und
  den erklärten Fähigkeiten, auch ob er dich fragen kann (Elicitation). Die Tools sehen dieselbe Beschriftung.
  Doku: eine Tabelle, was Claude Code, Claude Desktop, Cowork und VS Code unterstützen.
- **Benannte MCP-Tokens, eins pro Client** (`llm-cli mcp token add|list|rotate|revoke`): Ein Token wird einmal
  gezeigt und nur als SHA-256 gespeichert (`mcp_tokens.json`, Modus 0600). Sein Name ist der Aufrufer im Audit-Log
  (`mcp:<Name>`), und es hat eigene Berechtigungen: Ein neues Token darf nur lesen (nie ein Tool, das deine
  gemeinsamen Berechtigungen oder die eigene Vorgabe des Tools verweigern), bis du mit
  `llm-cli tools permissions --token NAME` mehr erlaubst (nicht zusammen mit `--caller`);
  `--policy-from mcp|ditto|<Token>` beginnt mit einer Kopie der Entscheidungen und gesehenen Tools dieses
  Aufrufers, `--link ditto` nutzt Dittos Berechtigungen per Verweis (für Ditto auf einem anderen Rechner).
  Demselben Namen gehören die Builds und Jobs, die der Client startet; er zählt seine Wartevorgänge und wird beim
  Ende seiner Sitzung protokolliert. Über stdio
  setzt der Server-Eintrag des Clients `LCF_MCP_TOKEN`; der Server liest es einmal, entfernt es aus seiner Umgebung
  und startet nicht mit einem ungültigen Wert. Rotieren oder Widerrufen wirkt sofort, auch für einen laufenden
  Server (die Aufrufe, die ein widerrufener stdio-Client noch schickt, werden abgelehnt und protokolliert); ein
  widerrufenes Token verliert seine Berechtigungen. Tokens verwaltet nur ein Mensch an einem Terminal (protokolliert
  mit dem Akteur `cli`, nie das Token selbst), und ein Token, das in ein Tool-Argument, eine Logzeile oder das
  Audit-Log gerät, wird wie andere Schlüssel geschwärzt.
- **Wartende Freigaben für KI-Clients, die dich nicht fragen können** (MCP-Server). Hat ein Client keine
  Formular-Elicitation (Claude-Desktop-Chat, Cowork), ist eine Frage länger als 4.000 Zeichen oder hast du ein Tool
  mit `llm-cli tools permissions --confirm-in-framework TOOL` gesetzt (oder „Ask in the framework“ in der GUI),
  wartet die Frage im Framework auf dich: Ein Hinweis in der Statusleiste der GUI öffnet
  **Tools > Pending Approvals...**, und `llm-cli tools approvals` (`list`, `show`, `approve`, `decline`; braucht ein
  Terminal) funktioniert auf einem Rechner ohne Bildschirm. Der Client bekommt `waiting_for_approval` mit einer
  Job-ID; sobald du freigibst, führt das Framework den Aufruf selbst unter dem Aufrufer des Clients aus, außer seine
  Frage würde inzwischen anders lauten (dann wirst du erneut gefragt), und der Client liest das Ergebnis mit
  `get_job_status`. Eine Freigabe ist an Aufrufer, Tool, Argument-Hash und Fragen-Hash gebunden, läuft einmal,
  verfällt nach 15 Minuten und endet mit der Sitzung des Clients, `cancel_job` oder dem Beenden des Frameworks; ein
  Client hat höchstens 5 offen; die Frage zeigt zuerst die Aufruferklasse und den Namen des Clients als selbst
  angegeben, bis zu 16.000 Zeichen. Das Fenster setzt nie einen anderen Aufruf an die Stelle dessen, den du gerade
  liest, und nachdem sich dessen Frage geändert hat, wartet die Freigabe einen Moment. Entscheidungen werden
  festgehalten, bevor sie gespeichert werden, und die Datei der Freigaben ist signiert wie die Berechtigungen.
- **Dauerhafte Freigaben: „ja, und nicht mehr fragen“** (MCP-Server, Ditto). Nur von dir im eigenen Fenster des
  Frameworks angelegt (das Freigabefenster, `llm-cli tools approvals approve ID --always`, Dittos Dialog und
  Rückfrage), nach einer Warnung, die nennt, wer dann ohne Nachfrage handeln darf, was in einfachen Worten abgedeckt
  ist, wie weit und wie lange. Das Formular eines MCP-Clients kann das Framework nur darum bitten, was du in dessen
  Fenster bestätigst (eine offene Anfrage je Aufrufer und Tool; ein abgelehnter Rahmen wird 24 Stunden lang nicht
  erneut angefragt). Rahmen kommen aus Fakten, die das Framework nachgeschlagen hat: Zoo-Pakete nur für Wissens- und
  Rezept-Pakete (Vertrauensstufe verified oder community) und verifizierte Target-Pakete, gebunden an Art,
  Vertrauensstufe und Index-Schlüssel; Builds und Downloads nur für safetensors oder GGUF, ohne Zugangsbeschränkung,
  freizügige Lizenz, bis zu einer Größenklasse; nie für Deployment, Einstellungen, Crawlen, das Anlegen von Modulen,
  `export_pack`, Healing, Löschen oder Wiederherstellen und das Stoppen der Arbeit eines anderen Clients. Alle lokalen
  stdio-Clients zusammen bekommen nur den engsten Rahmen, eine Warnung und höchstens 30 Tage. Gespeichert in der
  signierten Berechtigungsdatei und nur auf deine Handlung hin geschrieben; Nutzungen kommen aus dem Audit-Log
  (`grant:<id>`), jede Nutzung zeigt in der GUI einen Hinweis mit **Revoke**, der nennt, wer gehandelt hat, und das
  Tool; sie verfallen nach 90 Tagen ohne Nutzung und bleiben verfallen. Die Anfrage eines Tokens, das seitdem
  widerrufen wurde, wird nie zur dauerhaften Freigabe eines neuen Tokens mit demselben Namen.
  `llm-cli tools permissions --grants`, `--revoke-grant ID` und der Dialog Tool Permissions führen sie auf
  und widerrufen sie; solange die GUI läuft, kann die Kommandozeile keine anlegen. Noch bietet kein aktuelles Tool
  eine an: Die Regeln kommen mit den Tools, deren Fakten das Framework nachschlägt.
- **MCP-Ressourcen**: `lcf://targets`, `lcf://targets/<Target>/capabilities`, `lcf://builds/<ID>/status`,
  `lcf://builds/<ID>/log`, `lcf://permissions` und die Dokumente der Installation `lcf://docs/<Name>` (CLI_Usage,
  Features, Module_Developer_Guide, BLUEPRINT und ihre `_DE`-Zwillinge, aus einer festen Liste). Eine Ressource wird
  über die Tool-Registry gelesen wie das Lese-Tool, das sie spiegelt: Die Berechtigung dieses Tools entscheidet (ein
  verbotenes Tool verbirgt seine Ressourcen, „fragen“ lehnt mit dem aufzurufenden Tool ab), derselbe Ausgabefilter
  bereinigt das Ergebnis und packt externen Text als nicht vertrauenswürdige Daten ein, ein Lesen zählt zur
  Leserate der Lese-Tools (ein Limit für Lese-Tools, Ressourcen und Prompts zusammen), und jedes Lesen landet als
  `read_resource:<URI>` im Audit-Log. Unbekannte IDs, Dokumente außerhalb der Liste und Parameter mit `..`, einem
  absoluten Pfad oder einem NUL-Byte sind ungültige Parameter (-32602) und landen ebenfalls im Audit-Log, auch eine
  unbekannte URI oder ein anderes Schema, die der Server ablehnt, bevor eine Ressource läuft. Clients mit Protokoll
  2026-07-28 können auf Änderungen hören: Die Listen der Tools, Prompts und Ressourcen ändern sich mit den
  Berechtigungen, und die Status- und Log-Ressourcen eines Builds melden jede Änderung an den Aufrufer, der den Build
  gestartet hat, über den eigenen Ereignisbus des Servers.
- **MCP-Prompts**: Dittos Aufgaben für einen Client, der Dittos Arbeit übernimmt, mit demselben Aufgabentext und
  Wissenskontext: `ask_about_framework`, `convert_model_for_board`, `recommend_build_settings`,
  `diagnose_build_failure`, dazu `add_board` und `deploy_to_board`, die angeboten werden, sobald es die Tools gibt, zu
  denen sie führen. Die erste Nachricht ist der feste Text des Frameworks, die zweite der Kontext, in dem jeder Wert
  von außen (auch die Argumente) als nicht vertrauenswürdige Daten eingepackt ist, Secrets geschwärzt, höchstens
  24.000 Zeichen. Ein Prompt wird wie ein Lese-Tool gelesen: Die Leseberechtigung entscheidet, ein unbekanntes Argument
  wird mit den gültigen Namen abgelehnt, ein Abruf zählt zur selben Leserate, jeder Abruf landet als
  `get_prompt:<Name>` im Audit-Log. Ein Prompt, dessen
  Kontext enthält, was ein Lese-Tool liefert (Zustand und Log eines Builds, die Capabilities eines Targets), folgt
  auch der Berechtigung dieses Tools (verborgen, solange es verboten ist, abgelehnt bei „fragen“), und Auszüge aus
  der Wissensbasis kommen nur, solange `search_knowledge` erlaubt ist. Vervollständigung
  (VS Code) bietet Target-IDs, Boards, Dokumentnamen und die eigenen Build-Requests des Aufrufers an.
- **Ehrliches „kann ich nicht“ für Dittos Modulvorschläge und Build-Empfehlungen**: Das Modell darf mit `cannot` und
  einem Grundcode antworten (fehlende Information, nicht unterstützt, kein Tool, Berechtigung, braucht einen Menschen,
  Hardware, Ressourcen, Limit; „ungetestet“ ist keiner davon). Ditto fragt noch einmal, wenn die Prüfung des
  Frameworks in den Eingaben der Aufgabe findet, was die Aufgabe braucht, und zeigt ein zweites `cannot` als vom
  Framework nicht bestätigt; ohne Probe und ohne Board sagt es das selbst, ohne ein Modell zu fragen. Modulvorschläge
  bekommen `assumptions` (die geratenen Werte, höchstens 10) und `base_image_reason` (Pflicht für ein Image, das kein
  Docker Official Image ist) und können ohne Probe vom Board und seinem SoC ausgehen. Der Assistent und
  `llm-cli module generate-ai` zeigen das Basis-Image offen, bevor das Modul angelegt wird: Registry, Digest,
  offizielles Image oder fertiges Image einer anderen Registry, Netzwerkzugriff des Image-Builds und den Grund der KI.
- **MCP-Tokens in der GUI**: Der Dialog Tool Permissions (Tools > Tool Permissions (Ditto / MCP)) hat einen
  Abschnitt „MCP tokens“, der die Tokens auflistet und eines anlegt (einmal gezeigt; nur lesend, eine Kopie der
  Berechtigungen der lokalen stdio-Clients oder Dittos, oder an Dittos gebunden), rotiert, widerruft und die eigenen
  Berechtigungen jedes Tokens festlegt, protokolliert mit dem Akteur `gui`, nie mit dem Token.
- **Hardware-Probes über ihre ID für KI-Clients und Ditto**: `llm-cli scan --import DATEI [--label TEXT]` (und
  **Import from File** in der GUI und die Importe des Modul-Assistenten) legt die Probe-Datei eines Boards im
  Probe-Speicher ab, geprüft und ohne Hostnamen, und zeigt ihre Probe-ID und das passende Target. Das Lese-Tool
  `get_hardware_probe` zeigt die neuesten Probes mit ihrem passenden Target oder die Werte einer Probe (Text von
  außen); die Ressource `lcf://probes/{probe_id}` spiegelt es; `probe_local_hardware` (fragt standardmäßig; „nicht mehr
  fragen“ wird angeboten) untersucht diesen Rechner als Job und behält das Ergebnis als `local`. Der Prompt
  `add_board` wird jetzt angeboten und liest eine Probe über ihre ID. Kein Tool liest eine Probe über einen Pfad.
- **`create_target_module`**: Ein KI-Client in Dittos Rolle (oder Ditto) legt ein Target-Modul für ein Board an, das
  hier vielleicht niemand testen kann, durch die Prüfungen des Modulgenerators. Die Frage zeigt das Basis-Image,
  festgelegt auf den Digest, auf den sein Tag gerade zeigt (jede Registry; eine nicht offizielle klar als fertiges
  Image ihres Herausgebers; der Image-Build läuft mit Netzzugang), die Plattformen des Images und ob eine zu diesem
  Rechner passt, die Begründung der KI, jede Hersteller-Quelle, jedes pip-Paket und jeden Download, die geschätzten
  Werte und dass das Modul ungetestet ist; das Modul wird mit genau diesem Digest geschrieben, mit Support-Stufe
  `community`, `ai-generated` und dem aus der Probe des Boards geplanten `runtime:`-Block. Es fragt immer, bietet nie
  „nicht mehr fragen“ an, und eine lange Definition wird zu einer ausstehenden Freigabe.
- **Hersteller-Software in Target-Modulen**: `package_sources` (eine https-Paketquelle für apt, deren
  Signaturschlüssel der Image-Build gegen seinen Fingerabdruck prüft), `pip_packages` (festgelegt als
  `name==version`, von PyPI oder einem https-Index) und `pinned_downloads` (https, genaue Größe und sha256, nach
  `/opt/vendor`), eingefügt von der Dockerfile-Vorlage des Frameworks, für Dittos Vorschläge, den Assistenten und das
  MCP-Tool. Ihre Anzahlen sind weiche Grenzen (3, 30, 5): Darüber zu gehen ist eine Warnung, über die die Zustimmung der
  Person hinweggeht, nie eine Ablehnung; ein vorgebautes llama.cpp bleibt abgelehnt.

- **Deployment für KI-Clients** (MCP-Tools `list_devices`, `create_deploy_package`, `deploy_to_device`, Prompt
  `deploy_to_board`; Entscheidung 2): Ein Client listet die Geräte, denen du vertraut und die du gekoppelt hast,
  paketiert ein gebautes Modell als Job (die Platte muss das Doppelte des Modells fassen) und überträgt es als Job.
  Bevor das Deployment erfragt wird, liest das Framework den Host-Schlüssel des Geräts (keine Anmeldung) und lehnt
  einen unbekannten oder geänderten Schlüssel oder ein nicht gekoppeltes Gerät ab; die Frage zeigt das Gerät mit
  deinem Namen dafür, den Fingerabdruck des Host-Schlüssels, das sha256 des Pakets und das Vertrauen in sein Target,
  und das Deployment bricht ab, wenn Paket oder Schlüssel danach abweichen. Es wird immer erfragt, nie von einer
  Daueranfrage abgedeckt, eins je Gerät zur Zeit; `cancel_job` schließt die SSH-Sitzung und meldet den Zustand des
  Geräts als unbekannt. Die Ausgabe des Geräts kommt als externer Text markiert zurück (`get_job_status`-Feld
  `external_text`). Keine Eingabe nennt Adresse, Benutzer, Port, Passwort, Fingerabdruck oder Pfad.
- **Vertraute Geräte** (`~/.llm-framework/devices.json`, `llm-cli deploy devices list|add|remove`, `deploy run
  --label`): `deploy run`, `scan --device`, `device runtime deploy` und das Deployment-Fenster der GUI tragen ein
  Gerät ein, nachdem du seinen Host-Schlüssel bestätigt hast und sich das Framework angemeldet hat (im Audit-Log
  vermerkt); `devices add` übernimmt ein Gerät, dem du schon vorher vertraut hast (nur im Terminal, nie ein neuer
  Schlüssel, nie ein Passwort). Nur diese Geräte können KI-Tools nennen, und nur, solange die `known_hosts` des
  Frameworks noch genau diesen Schlüssel enthält; kein Tool schreibt einen Eintrag.

- **Optionale Laufzeit-Tools für KI-Clients** (MCP-Tools `build_local_runtime`, `start_local_runtime`,
  `stop_local_runtime`, `run_benchmark`, `probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark`;
  Ressource `lcf://runtime/catalog`; Prompt `set_up_offline_assistant`): mit ihren Laufzeitpaketen registriert und
  verborgen, bis du sie freigibst, auch wenn die ganze Stufe erlaubt ist. Der lokale Server ist immer aus dem
  festgelegten Commit selbst gebaut (keine Prebuilt-Option, keine CMake-Option und kein Befehl als Eingabe); ein
  fehlender Toolchain-Teil wird mit `needs_person` und `llm-cli ditto runtime install` abgelehnt; gestartet wird nur ein
  geprüfter Build, und kein Ergebnis enthält seine Adresse oder seinen Schlüssel; eine abgebrochene Installation wird
  nie aktiv, und ein abgebrochener Modell-Download behält seine `.part`-Datei. Ein nicht vertrauenswürdiges Modell wird
  nativ nur nach deiner Zustimmung im eigenen Fenster des Frameworks gemessen, die zuerst ins Audit-Log geschrieben
  wird, oder in der Docker-Sandbox. Die Board-Tools erreichen nur vertraute Geräte mit dem vertrauten Host-Schlüssel,
  werden immer erfragt und nie von einer Daueranfrage abgedeckt.
- **Benchmark-Läufe dieses Rechners** (`~/.llm-framework/benchmarks/`): `llama-bench`-Ergebnisse des selbst gebauten
  Builds (Prompt- und Erzeugungsgeschwindigkeit, höchster Speicherbedarf) mit dem sha256 des Modells und der Build-ID;
  `get_status` zeigt die neuesten.

### Geändert
- **Abgelehnte MCP-Aufrufe tragen ihren Grund-Code als strukturierten Inhalt** (`{"error": {"code", "field"}}`, nur
  Fakten des Frameworks, der Text unverändert). Ditto im MCP-Client-Modus liest Code und Feld daraus, statt sie aus
  den Worten des entfernten Servers zu raten; ein unbekannter Code ist `failed`, ein Feld außerhalb des Schemas des
  Tools wird verworfen.
- **`ArtifactStore` (`delete_artifact`) nutzt nur öffentliche Methoden von `BuildService`** (`output_folder`,
  `artifact_entries`, `busy_output_folders`, `ArtifactStore.for_service`).
- **Der Deep-Ingest-Hinweis hält wieder fest, wer ihn gelesen hat**: Seine Zustandsdatei speichert, wo er bestätigt
  wurde (`via`: `gui` oder `cli`), und das Lesen landet im Audit-Log (Akteur `gui` oder `cli`). Nur eine Person
  bestätigt ihn; `CrawlNotice.acknowledge` lehnt jeden anderen Weg ab.
- **Ein Target-Pack übernimmt nie den Namen eines anderen Targets**: Ein Target-Pack, dessen ID (oder angegebene
  Target-ID) ohne Rücksicht auf Groß- und Kleinschreibung und Trennzeichen der eines vorhandenen Targets gleicht, wird
  abgelehnt, im Community Hub, in der CLI und über MCP.
- **Builds und Downloads teilen eine Speicherplatz-Reservierung; Bereitstellung über den Abrufer des Frameworks**
  (Reste von MCP-Phase T, Runde 1): `download_hf_file` reserviert seinen Speicherplatz in dem prozessübergreifenden
  Verzeichnis, das Builds nutzen. Ein Build stellt sein Hugging-Face-Modell über den Abrufer des Frameworks bereit
  (fortsetzbare `.part`-Dateien, jede Datei gegen Größe und sha256 des Hubs geprüft, durch einen Abbruch binnen
  Sekunden gestoppt) statt über `hf_hub_download` und lehnt ab, wenn sich der Commit nicht ermitteln lässt. Ein
  Laufwerkswächter stoppt das Bereitstellen und die Konvertierungs-Container, wenn das Cache-Laufwerk unter die halbe
  Reserve fällt. `start_build` bietet einen Dauerauftrag im Rahmen des Entwurfs an (mitgeliefertes oder verifiziertes
  Target, safetensors oder GGUF, nicht zugangsbeschränktes Modell und Basismodell, freizügige Lizenz, offizielles
  Basis-Image, Größenklasse), nur wenn das Laufwerk die größere Reserve behält. `report_cannot_do` zählt nur die
  Aufrufe der laufenden Sitzung, und Hugging-Face-Abfragen von `check_build_request` zählen zur Rate von 30 pro Stunde.
- **Eine Hugging-Face-Einordnung für Builds und Downloads**: `check_build_request` und `start_build` lesen Lizenz, Zugangsbeschränkung und Dateien eines Modells jetzt nach denselben Regeln wie `get_hf_model_info` und `download_hf_file` (Lizenzen wie GPL, DeepSeek oder Qwen Research waren auf dem Build-Weg „unbekannt“ und auf dem Download-Weg „einschränkend“), und beide halten dieselbe Speicherreserve frei.
- **MCP über HTTP**: Ein Token hält höchstens 4 der 8 Sitzungen, damit ein Client die anderen nicht aussperren kann;
  eine Sitzung schließt nach 10 Minuten ohne Anfrage (die Vorgabe des SDK sind 30); eine noch offene Anfrage, wenn ihr
  Token widerrufen oder rotiert wird (ein Warten, ein Ereignisstrom, ein Listen-Strom), wird sofort geschlossen, und
  ebenso die Sitzungen dieses Tokens: Eine Sitzung gehört dem Token-Wert, der sie geöffnet hat, sodass der Client nach
  einer Rotation eine neue öffnet und die alten weder seinen Anteil noch die Plätze des Servers belegen.
- **Dittos Aufgabentexte** (Chat mit Tools, Healing-Analyse, Modulvorschlag, Build-Empfehlung) stehen in einem Modul,
  `orchestrator/llm/task_prompts.py`, das sie mit den MCP-Prompts teilen, und jeder trägt die Regel für das ehrliche
  „kann ich nicht“: den konkreten Grund nennen, nie ein Ergebnis behaupten, das man nicht bekommen hat, und
  ungetestete Hardware ist kein Grund abzulehnen. Die Modulaufgabe beschränkt Basis-Images nicht mehr auf `debian:*`
  und `ubuntu:*`: Sie bevorzugt ein Docker Official Image mit den Paketen des Herstellers und begründet jedes andere
  Image. Ein von Ditto vorgeschlagenes Modul sagt in seiner Beschreibung, dass es auf echter Hardware ungetestet ist,
  und nennt die Annahmen, auch eines, das im Modul-Assistenten aus Dittos Vorschlag angelegt wird.
- **Ditto läuft auf der neuen Anbieterschicht** (offizielle SDKs anthropic und openai) statt auf LiteLLM, und das
  eingebaute Transformers-Modell für den Offline-Modus ist entfernt (der Offline-Modus nutzt lokale Anbieter:
  Ollama, den verwalteten llama-server, einen Server in deinem Netzwerk). Healing-Analysen, Modulvorschläge und
  Build-Empfehlungen sind geprüfte strukturierte Antworten; eine ungültige Antwort ist ein Fehler und wird nie
  angewendet.
- Der **Chatverlauf** wird nur noch ergänzt, nie überschrieben, und je Benutzer geführt; ein langes Gespräch wird
  in einem neuen zusammengefasst. Verläufe früherer Versionen werden übernommen.
- Fehler der Anbieter werden in einfacher Sprache erklärt, z. B. ein Claude-API-Schlüssel, der eine Workspace-ID
  braucht, ein Workspace, den der Schlüssel nicht nutzen darf, ein falscher, widerrufener oder abgelaufener
  Schlüssel oder ein `ant auth login`-Profil, das eine neue Anmeldung braucht.
- Mehr Fehler der Anbieter sagen, was passiert ist und was zu tun ist, mit einem Code für GUI und CLI: kein
  Guthaben oder ein Ausgabenlimit in der Claude Console, ein abgelehnter OpenAI-Schlüssel oder ein aufgebrauchtes
  OpenAI-Kontingent, ein Gemini-Schlüssel, den Google ablehnt, ein OpenRouter-Schlüssel an seinem Kreditlimit, ein
  lokaler Server, der nicht läuft oder einen Schlüssel verlangt, Ollama ohne seine Anmeldung bei ollama.com, ein
  gesperrter Schlüsselbund und ein unlesbarer Tresor. Ein Claude-Ausgabenlimit wird nicht mehr wie ein
  Ratenlimit wiederholt.
- **Anmeldung bei der Claude-API**: Ein gespeicherter API-Schlüssel wird immer zuerst genutzt. Ein
  `ant auth login`-Profil wird nur verwendet, wenn du es auswählst (`ai_auth_anthropic: ant_profile`); bisher
  ersetzte ein Profil auf der Festplatte stillschweigend den gespeicherten Schlüssel und damit das Konto, das
  abgerechnet wird. Der frühere Name `oauth` bedeutet weiterhin das Profil, in der Einstellung und in einem
  Anbieterkatalog in deinem Konfigurationsordner.
- Dittos Prompts: Build-Fehler werden richtig der Build-Maschine oder dem Gerät zugeordnet, Build-Empfehlungen
  wissen, welche Eingaben jedes Format annimmt (ein ONNX-Modell wird nicht an GGUF geschickt), Fragen zu Targets
  und Builds werden aus den Tools beantwortet, und Modulvorschläge verwenden Compiler-Flags statt der Liste der
  CPU-Features.
- Lokales Modell: Aufwand „low“ schaltet das Nachdenken (Thinking) ab (Qwen3.5 auf llama-server denkt sonst in
  voller Länge, was auf einer CPU Minuten dauert); ab „medium“ bleibt es an.
- Abhängigkeiten: `anthropic` 1.13.0, `openai` 3.28.0 (vorher 2.54.0), `fastembed` 0.8.1 (lokale Embeddings, ONNX
  auf der CPU) und `pyrage` 1.4.0 (age-Verschlüsselung privat geteilter Community-Packs) sind exakt gepinnt;
  pip-audit meldet keine bekannten Schwachstellen.
- **Dittos Wissensauszüge passen in den Kontext**: Sie belegen höchstens ein Drittel von `chat_context_limit`; ein
  Auszug, der nicht passt, wird übersprungen, und ein zu langer erster wird an einer Zeile gekürzt (ein offener
  Codeblock wird geschlossen).
- **Ergebnis von Deep Ingest**: Es meldete „Deep Ingest complete“, selbst wenn jede URL fehlschlug. Jetzt meldet
  es Erfolg (alles gespeichert), teilweise (einige Seiten gespeichert; andere schlugen fehl, waren von der Site
  vorbehalten, oder der Crawl wurde gestoppt) oder Fehlschlag (nichts gespeichert), jeweils mit eigener Meldung und
  erst, nachdem der letzte Schreibvorgang zurückgekehrt ist. Seiten werden gespeichert, sobald sie gelesen sind;
  ein gestoppter oder unterbrochener Crawl behält also, was er schon gespeichert hat, und Seiten, die nicht
  abgerufen werden konnten, stehen im Crawl-Protokoll.
- Der User-Agent des Crawlers nennt die echte Version des Frameworks (er nannte 1.6.0).
- Deep Ingest: Ein Link, der auf einen anderen Host weiterleitet, wird wie jeder andere externe Link übersprungen;
  nur eine Weiterleitung der Start-URL kann den Crawl auf einen anderen Host verlegen.
- Wissensexporte und mit ihnen der Wissens-Snapshot neuer Target-Module werden bereinigt: Home-Ordner, IP- und
  MAC-Adressen, E-Mail-Adressen, JWTs, der Hostname und der Benutzername werden entfernt, zusätzlich zu
  API-Schlüsseln. Image-Digests (`name@sha256:...`) und die Pfade von Standardkonten wie `/root` oder
  `/home/runner` bleiben, wie sie sind, sodass ein bereinigtes Target weiterhin baut.
- Der Wissens-Snapshot eines neuen Target-Moduls ist freiwillig (ein Kontrollkästchen auf der letzten Seite des
  Assistenten) und enthält nur die Seiten, die im Assistenten für dieses Target gecrawlt wurden, und zwar alle:
  Finish bleibt gesperrt, solange ein Crawl läuft. Vorher ging die ganze lokale Wissensbasis in jedes Modul. Eine
  `README.md` daneben sagt, was er enthält und dass das Framework ihn nicht einliest; ließ sich nichts anhängen,
  sagt der Assistent das.
- Target-Module, die mit dem Assistenten, `llm-cli module create` oder Ditto erstellt werden, bekommen einen Block
  `capabilities:` (Support-Level community: ihre Boards und GGUF mit den Typen, die ihr Build-Skript verarbeitet),
  sodass Builder, CLI und KI-Clients ihre Build-Einstellungen prüfen. Die Erzeugung schlägt fehl, wenn irgendwo ein
  Platzhalter der Vorlage übrig bleibt, außer im Wissens-Snapshot, dessen gecrawlte Seiten Text wie `YYYY-MM-DD`
  enthalten dürfen.
- **Tresor für Zugangsdaten im Format v2**: Jeder Eintrag trägt eine Schlüssel-ID, und der Schlüssel des Tresors
  lässt sich wechseln (jeder Eintrag wird neu verschlüsselt, der alte Schlüssel entfernt; ein unterbrochener
  Wechsel wird beim nächsten Start abgeschlossen). Jeder Tresor hält seinen Schlüssel in einem eigenen Eintrag im
  Schlüsselbund, sodass ein Schlüsselwechsel oder ein anderer Schutz nie einen anderen Tresor berührt (etwa den
  eines anderen `LLM_FRAMEWORK_HOME`-Ordners); ein Speicher einer früheren Version zieht beim ersten
  Schlüsselwechsel in seinen eigenen Eintrag um. Anbieterschlüssel werden als Datensätze unter
  `cred/<anbieter>/api_key` gespeichert; Speicher und Schlüsselnamen früherer Versionen werden wie bisher gelesen
  und bei der nächsten Änderung umgestellt.
- Das Lesen eines Geheimnisses wird einmal je Prozess und Name ins Audit-Log der Secrets geschrieben, nicht bei
  jedem Zugriff.
- Befehle, die den Tresor brauchen, enden mit Exit-Code 5, wenn er sich nicht nutzen lässt (kein nutzbarer
  Schlüsselbund, keine Passphrase, ein beschädigter Tresor), statt mit 1, und nennen die Möglichkeiten, auch
  `llm-cli ditto auth storage --use ...`.
- Fehlermeldungen und Hinweise nennen `llm-cli ditto login <anbieter>` statt
  `llm-cli secrets set <Schlüsselname>`; `llm-cli module generate-ai` nutzt die gespeicherte Anmeldung und fragt
  nie selbst nach einem Schlüssel.
- `scripts/hardware_probe.sh` und `scripts/hardware_probe.ps1` schreiben Schema 2 mit denselben Schlüsseln wie die
  Python-Probe (Tests führen die `.sh` und die `.py` auf denselben Testrechnern aus). Beide sind standardmäßig nicht
  interaktiv: Die Paketinstallations-Abfrage der `.sh` braucht `--install-tools` (sie bietet jetzt `vulkan-tools` an,
  das einzige Hilfsprogramm, das die Probe nutzt), und die `.ps1` startet sich nur mit `-Elevate` mit erhöhten Rechten
  neu. Die `.sh` läuft in jeder POSIX-Shell (dash, BusyBox ash), und `llm-cli scan --script` und der GUI-Scan dieses
  PCs führen sie mit `sh` aus, also geht es auch ohne bash; sie liest `/proc` und `/sys` direkt statt über `lspci` und
  `lsusb` und schreibt alle GPUs; die `.ps1` liest den nutzbaren Speicher statt der Summe der Speicherplätze.
- Target-Images bauen den llama.cpp-Pin aus `configs/llama_runtime.yml` per Commit (`b11429`, `d8123504`): Die
  Engine übergibt Tag und Commit, und das Image prüft den Commit. Die Einstellung `llama_cpp_version` hat als Vorgabe
  den Tag des Pins statt eines fest geschriebenen `v0.6.0` und ändert nicht mehr, was gebaut wird (ein anderer Wert
  ergibt eine Warnung im Build-Log). Die Dockerfiles, die Build-Argumente in `target.yml` und
  `configs/project_sources.yml` (dort stand noch `b4009`) nennen denselben Tag und Commit.
- Die Target-Vorlage baut die Konvertierungswerkzeuge von llama.cpp mit denselben Schritten wie das Rockchip-Image,
  ein erzeugtes Modul bricht also nicht mehr mit „llama.cpp converter not found“ ab. Die GPU-Vorlage nimmt CUDA 12.9.1
  aus den Redist-Archiven von NVIDIA (Manifest per sha256 festgelegt, jede Komponente geprüft) auf Ubuntu 22.04, per
  Digest festgelegt, statt der nicht festgelegten `nvidia/cuda:12.2.2`-Images; das vorgebaute Entwicklungs-Image von
  NVIDIA ist ein ausdrücklicher Opt-in, per Digest festgelegt (ein Basis-Image, das du selbst wählst; Dittos
  Vorschläge enthalten nie eines, weil eine Probe-Datei sie lenken könnte) und gekennzeichnet mit
  `org.llm-framework.cuda-source=prebuilt-image`, in den Logs mit „not self-built“. Der Assistent trägt für NVIDIA-Hardware kein `nvidia/cuda`-Image
  mehr ein.
- `deploy.sh` eines GGUF-Pakets schlägt keinen `llama-server` mehr vor, der im `PATH` des Boards gefunden wird: Es
  nennt das Startskript der selbst gebauten Geräte-Laufzeit, wenn sie installiert ist, und sonst, wie man sie
  deployt (`llm-cli device runtime build` und `deploy`). `llm-cli scan --device` merkt sich den SSH-Benutzer als
  Deploy-Benutzer des Boards (nie root).
- Die Perplexity-Prüfung des Dataset-Managers nutzt das selbst gebaute `llama-perplexity` dieses Rechners, statt
  `ghcr.io/smilez1985/llm-runtime` zu holen: Katalogmodelle nativ; jedes andere Modell nativ nur nach deiner
  Zustimmung, die zuerst ins Tool-Audit-Log geschrieben wird; die Docker-Sandbox (das eigene, lokal gebaute Image des
  Builds, ohne Netzwerk) auf Wunsch. Eine gescheiterte Messung ist ein Fehler mit Grund statt der Werte 999.99 oder
  888.88.
- **MCP-Ergebnisse**: Der Textblock eines Tool-Ergebnisses beginnt mit einem Satz, der nur aus Fakten des Frameworks
  besteht (IDs, Zustände, Anzahlen), gefolgt vom JSON, eingepackt als nicht vertrauenswürdige Daten;
  Ausgabeschemas markieren jedes Feld mit externem Text (`"untrusted": true`); Ergebnisse tragen `notes`, die das
  Framework schreibt (zum Beispiel „die Wissensbasis ist erreichbar; kein Auszug passte“ oder dass nur die Builds
  dieses Prozesses aufgeführt sind); `suggested_step` eines offenen Self-Healing-Vorschlags heißt jetzt
  `manual_step_text`, Text zum Lesen für die Person. Die Serveranweisungen sagen, wie markierte Felder zu behandeln
  sind und dass der Client eine Aufgabe, die er nicht erledigen kann, mit dem konkreten Grund meldet.
- **MCP-Clients sehen nur die Tools, die sie nutzen dürfen**: Tools, die deine Berechtigungen verweigern, fehlen in
  der Tool-Liste (dieselben Tools, die Ditto bei denselben Berechtigungen bekommt); ein Aufruf eines solchen Tools
  wird trotzdem abgelehnt und festgehalten. `destructiveHint` folgt der eigenen Angabe jedes Tools statt seiner
  Stufe (`resolve_healing` ist zerstörend, weil eine seiner Korrekturen den Docker-Build-Cache löscht), und Tools,
  die andere Geräte erreichen, bitten den Client, bei jedem Aufruf seine eigene Rückfrage zu zeigen
  (`anthropic/requiresUserInteraction`).
- Eine lokale Modelldatei wird allein in die Build-Container eingebunden, nicht mehr zusammen mit dem Ordner, in
  dem sie liegt (ein Modell, das Dateien daneben braucht, etwa externe ONNX-Daten, wird als Ordner angegeben). Eine
  RKNN-Beispielliste neben einer Modelldatei bekommt jetzt ihre eigene Ordnereinbindung.
- MCP und Ditto: Builds gehören dem Aufrufer, der sie gestartet hat (einer MCP-Verbindung, jedem benannten Token
  über HTTP, Ditto). Den eigenen Build
  mit `cancel_build` abzubrechen fragt nicht mehr; den Build eines anderen Clients abzubrechen folgt den
  Tool-Berechtigungen (standardmäßig fragt es). Eine Einstellung für das einzelne Tool gilt weiter, eine verweigerte
  Stufe bleibt verweigert, und eine beschädigte Berechtigungsdatei verbietet weiter. `get_build_status` zeigt die
  Aufruferklasse, die einen Build gestartet hat.
- **MCP über HTTP braucht ein benanntes Token je Client.** Das eine gemeinsame Token je Benutzer aus 3.0.0
  (`~/.llm-framework/mcp_http_token`) wird nicht mehr akzeptiert: Lege mit `llm-cli mcp token add NAME` ein Token je
  Client an, gib es dem Client und lösche die alte Datei. `llm-cli mcp serve --transport http` startet nicht, bevor
  es ein benanntes Token gibt.
- **Ein Client, der dich nicht fragen kann, wird nicht mehr abgewiesen**, wenn ein Tool auf „fragen“ steht: Der
  Aufruf wartet stattdessen im Framework auf deine Freigabe (siehe Hinzugefügt); bisher scheiterte er mit einem
  Hinweis, wie du das Tool erlaubst.
- **Bestätige deine Tool-Berechtigungen nach dem Update einmal.** Die Berechtigungsdatei ist jetzt signiert, und eine
  von 3.0.0 gespeicherte trägt keine Signatur, daher ist jedes Tool verboten, bis du die Berechtigungen neu einrichtest
  (die GUI zeigt den Dialog beim ersten Start und nennt den Grund; `llm-cli tools permissions`).
  `llm-cli tools permissions`, auch mit `--reset`, und `llm-cli ditto setup` brauchen eine Person an einem Terminal:
  Weitergeleitete Antworten werden abgelehnt.
- **Targets für KI-Aufrufer**: `list_targets` zeigt das Vertrauen jedes Targets (`shipped`, das Vertrauen eines
  Pakets, `ai-generated`, `own`) und ob es ungetestet ist; `get_target_capabilities` ergänzt das Vertrauen, die
  Annahmen und die Begründung des Basis-Images eines KI-erstellten Targets (Text von außen) und mit `check_files` die
  Probleme, die die Modul- und Paketprüfungen in seinen Dateien finden. KI-erstellte Targets tragen
  `provenance: ai-generated` und `untested: true` in `target.yml`, und ihre Builds warnen, dass sie ungetestet sind und
  ihr Image mit Netzzugang gebaut wird.
- **Probe und KI-Module ohne Terminal**: Die lokale Probe (`llm-cli scan`, `probe_local_hardware`) führt das
  Ersatzskript mit Eingabe aus `/dev/null`, `NON_INTERACTIVE=true` und eigenem Arbeitsordner aus und hört nach 120
  Sekunden auf. `llm-cli module generate-ai` legt das Basis-Image vor der Frage auf seinen Digest fest; es und der
  Assistent halten das gespeicherte Modul im Audit-Log fest (`module_saved`), und der Assistent gibt jedes Feld von
  Dittos Vorschlag weiter.

- **MCP: Builds werden mit dem gefragt, was das Framework nachgeschlagen hat** (Design T1, DECISIONS Runde 2 Punkt
  8): `start_build` zeigt den Hugging-Face-Commit, den es baut (und baut genau diesen), Downloadgröße, Art der
  Gewichte, Lizenz, Zugangsbeschränkung und den Zugriff des gespeicherten Tokens, den Platz auf jedem Laufwerk mit
  Konvertierungsreserve und dreifach gezählter Ausgabe, eine Schätzung, ob dieser Rechner und das Board es tragen,
  Einbindungen, Vertrauen des Targets und den Image-Build. Keine Größengrenze: Abgelehnt wird nur, wenn ein Laufwerk
  den Build neben einer Reserve und dem von anderen angenommenen Builds reservierten Platz nicht fasst
  (`insufficient_resources`, mit den Zahlen); Reservierungen liegen in `~/.llm-framework/disk_reservations.json` und
  werden geprüft, wenn ein Build die Warteschlange verlässt. Neues Lese-Tool `check_build_request`: derselbe Plan als
  Probelauf. `start_build` nimmt `optimization`.
- **MCP: Build-Abfragen mit ID und Label**: `list_artifacts` benennt gebaute Modelle mit einer ID und
  `output:<relativer Pfad>` mit Größe und Hash (seitenweise, gefiltert), das neue `get_artifact_details` liefert
  Manifest (externer Text), Model Card und Dateien, mit den Ressourcen `lcf://artifacts/{id}/manifest` und
  `/model-card`; `list_build_requests` (seitenweise) und `get_build_status` zeigen auch Builds anderer
  Framework-Prozesse aus ihren Build-Records, nur lesbar; `get_build_status` sagt, was die Anfrage baut;
  `read_build_log` liefert höchstens 1000 Zeilen und blättert mit `before_line` zurück.
- **Hugging-Face-Modelle werden in einem Ordner pro Commit bereitgestellt**,
  `<cache_dir>/models/hf/<owner>--<name>/<commit>/`, Datei für Datei unter einer Sperre; nur dieser Ordner wird in die
  Build-Container eingebunden (nicht mehr der ganze Modell-Cache), ein Abbruch stoppt den Download vor der nächsten
  Datei, und der Download stoppt, wenn das Cache-Laufwerk unter die Hälfte seiner Reserve fällt. Ein volles Laufwerk
  wird als `insufficient_resources` mit dem freien Platz gemeldet.

- **MCP: Status und Einstellungen des Frameworks** (Design T2, DECISIONS Runde 1 Punkt 4): `get_status` zeigt die
  Abschnitte Framework, Rechner, lokale Laufzeit (ein vorgebautes Archiv als „nicht selbst gebaut“ gekennzeichnet),
  Wissensbasis, KI-Zugang (nur Zustände, nie ein Schlüsselteil, Konto oder Endpunkt), Berechtigungen und die
  Audit-Einträge dieses Aufrufers; neu sind `get_settings` (eine feste Leseliste, Adressen als Schema und Host, Ordner
  als Labels) und `update_settings` (gewöhnliche Einstellungen mit einer Frage, die alten und neuen Wert zeigt,
  gespeichert in der `config.yml`; Einstellungen, die eine KI begrenzen, nur verschärfbar, und nur solche dürfen ohne
  Frage erlaubt werden, und ein Speicherlimit von 0, das Docker als keins liest, zählt als Lockerung; Berechtigungen,
  Geheimnisse, Tresor, Einstellungen des KI-Anbieters, Adressen und Ordner vor der Frage abgelehnt; kein Wert wird als
  „Standard“ gelesen).
- **MCP: ehrliches „kann nicht“, vom Framework geprüft** (DECISIONS Runde 3): Das neue `report_cannot_do` nimmt einen
  Grundcode (kein „ungetestet“) und was fehlt, prüft ihn an den Berechtigungen des Aufrufers, den Audit-Einträgen und
  Grenzen dieser Sitzung, dem Laufwerk und den Aktionen, die kein Tool tut, und antwortet confirmed, contradicted,
  premature (mit den Tools, die zuerst zu versuchen sind) oder unchecked, mit einem Satz nur aus Fakten des
  Frameworks (ein Tool nur mit seinem registrierten Namen); „die Hardware ist nicht verfügbar“ wird für jedes Tool widersprochen, das kein Gerät nutzt. Meldungen
  werden als `cannot_reported` protokolliert; `llm-cli tools audit --summary` zählt sie je Aufrufer.

- **Paketieren und Übertragen sagen, warum sie gescheitert sind**: `DeploymentManager` gibt den Grund zurück (kein
  Docker, ein nicht festgelegtes Image, ein geänderter Host-Schlüssel, der Exit-Status von `deploy.sh`) statt `None`
  oder `False` mit einer Logzeile (G23); `llm-cli deploy package` gibt ihn aus. Ein Paket wird unter einem vorläufigen
  Namen geschrieben und erst umbenannt, wenn es vollständig ist.

### Veraltet
- `llm-cli secrets set --password`: Ein Wert auf der Kommandozeile landet im Shell-Verlauf und in der
  Prozessliste. Nutze die verdeckte Abfrage oder `--with-token`.
- `llm-cli build start --parallel`: Die Option hatte nie eine Wirkung (ein Aufruf startet einen Build). Sie ist in
  der Hilfe ausgeblendet und gibt eine Warnung aus. `--optimization` bleibt; seine Hilfe sagt jetzt, dass das
  Rockchip-Target die Option nicht auswertet.

### Entfernt
- Dittos generierte Füll-Kalibrierungsdaten (Builds nutzen den mitgelieferten Kalibrierungstext oder deinen
  eigenen Datensatz).
- `litellm` und `tiktoken` und mit ihnen 19 indirekte Pakete (aiohttp, boto3, jinja2 und andere).
- Die ungenutzte Einstellung `input_history` (der Eingabeverlauf ist persönlich und liegt im Zustandsordner des
  Benutzers, nicht in der gemeinsamen `configs/config.yml`).
- Die Option *Respect robots.txt* im Deep-Ingest-Dialog und die Einstellung `crawler_respect_robots`: robots.txt
  ist ein maschinenlesbarer Vorbehalt wie die anderen und wird deshalb immer beachtet.
- Der „Swarm“-Upload: die XOR-„SwarmCipher“ (eine Verschleierung, die jeder rückgängig machen konnte),
  `llm-cli swarm upload`, der Knopf „Contribute to Swarm“ im Community Hub und seine Abfrage eines GitHub-Tokens.
  Das Framework fragt nie nach einem Repository-Token und speichert keins. Alte Swarm-Pakete in
  `community/knowledge/` werden mit einer Warnung übersprungen.
- Die Installation von Hand kopierter Modulordner aus `community/`: Community-Packs werden mit
  `llm-cli zoo install` oder dem Community Hub installiert.
- **Der Claude-Abo-Anbieter `claude_code`** (Ditto über `claude -p` mit den Limits eines Claude-Pro/Max-Abos):
  Anthropic erlaubt anderen Apps nicht, die Limits eines Claude-Abos ohne Genehmigung zu nutzen. Ein
  gespeichertes `ai_provider: claude_code` (oder `ClaudeCode`, `Claude Code`), derselbe Name in der Ersatzkette
  oder bei `--provider` bricht jetzt mit einer Meldung ab, die in den KI-Einstellungen einmal angezeigt wird und
  die Alternativen nennt: einen Claude-API-Schlüssel (Max- und Team-Abos enthalten monatliches API-Guthaben für
  eine verknüpfte Console-Organisation), Claude über Bedrock, Vertex oder Foundry, oder das Framework aus Claude
  Code selbst heraus nutzen (`llm-cli mcp config`). Es wechselt nie von sich aus zur Claude-API. Ein
  `claude_code`-Eintrag in einem Anbieterkatalog in deinem Konfigurationsordner wird mit einer Warnung
  übersprungen; die anderen Anbieter funktionieren weiter.
- Die Einstellung `preferred_tiny_model` des entfernten Transformers-Offline-Modus. Eine `config.yml`, die sie noch
  enthält, lädt wie bisher; der Wert wird ignoriert und bleibt in der Datei.
- Reste des entfernten Transformers-Offline-Modus und des Qdrant-Containers: der Download kleiner Modelle im
  Model-Manager (er las `source_repositories['tiny_models']`) und `DockerManager.ensure_qdrant_service` (die
  Wissensbasis nutzt ein eingebettetes Qdrant). `image_qdrant` bleibt: Deployment-Pakete bringen Qdrant auf das
  Gerät.

### Behoben
- **Die größere Speicherreserve eines Dauerauftrags wird unter der Sperre des Reservierungsbuchs geprüft** für
  `start_build`, `start_recipe_build`, `download_hf_file` und `add_pack_from_index`: die Reservierung wird mit der
  Reserve von 20 GiB / 10 % im Reservierungsbuch genommen, das alle Framework-Prozesse teilen, so dass eine
  Reservierung, die ein anderer Prozess zwischen Frage und Start genommen hat, mitzählt (vorher nur eine Momentaufnahme
  zur Zeit der Frage). `add_pack_from_index` nimmt jetzt überhaupt eine Reservierung und zählt die Reservierungen
  anderer Aufrufe schon vor der Frage.
- **Die Build-Frage nennt jedes Basis-Image des Targets**: vorher zeigte ein Build auf einem nicht mitgelieferten
  Target nur das erste `FROM` seines obersten Dockerfiles, so dass ein Hersteller-Image einer späteren Stufe
  (`COPY --from`, `RUN --mount=...,from=`) oder eines anderen Dockerfiles nie gezeigt, von einem bestätigten Build
  aber für den Build-Dauerauftrag als bestätigt vermerkt wurde. Jetzt wird jedes Image jedes Dockerfiles einzeln
  gezeigt (Registry, Digest, offiziell oder nicht), auch bei einem mitgelieferten Target, das ein Image außerhalb
  der Docker Official Images zieht.
- **GitHub-CLI und git der Pull-Request-Übergabe lesen nie stdin** (`stdin=DEVNULL`, G25): über stdio ist stdin
  des MCP-Servers der Protokollstrom.
- **Der KI-Import des Modul-Assistenten und die Modelllisten der KI-Einstellungen geben ihre Ergebnisse über das eine
  Worker-Relay an die GUI**: Der Assistent sendet nicht mehr aus einem einfachen Thread über ein selbst erzeugtes
  `WizardSignals`-Objekt ohne Eltern (ein geschlossener Assistent konnte es mitnehmen), und das eigene Relay der
  KI-Einstellungen ist in `WorkerRelay` aufgegangen.
- **Stopp-Texte von Deep Ingest** (Schaltfläche, Tooltip und die Meldung `crawl.stopping` auf Englisch und Deutsch)
  sagen, dass Stoppen auch eine Seite verwirft, die noch ankommt, statt „nach der aktuellen Seite“.
- **Ein beschädigtes `authors.json` verliert die beim ersten Kontakt festgehaltenen Autorenschlüssel nicht mehr**:
  Es bleibt als `authors.json.bad` erhalten, die Zusammenfassung der Installation warnt, dass der Schlüssel nicht
  verglichen werden konnte, und kein Schlüssel wird festgehalten (die Datei wird nie überschrieben), bis sie
  repariert oder entfernt ist.
- **Per Digest festgelegte Images in einem Deployment-Paket starten auf einem Board ohne Netz**: Ein über seinen
  Digest gezogenes Image hat kein Tag, wurde deshalb ohne Namen gespeichert, kam nach `docker load` als `<none>`
  zurück, und `deploy.sh` konnte es ohne Registry nicht über seine Digest-Referenz starten. Es wird jetzt unter
  `<Repository>:lcf-<12 Hex-Zeichen des Digests>` eingepackt und unter diesem Namen gestartet (ein hier gebautes Image
  behält sein eigenes Tag), und `deploy.sh` startet jedes eingepackte Image mit `--pull never`, sodass ein Board, auf
  dem ein Image fehlt, abbricht, statt eines aus einer Registry zu ziehen.
- **Hugging-Face-Abfragen verbergen keine Fehler mehr**: Der Modellmanager hinter dem Hugging-Face-Browser der GUI
  lieferte ein leeres Suchergebnis, eine leere Dateiliste oder eine „nicht einschränkende“ Lizenz, wenn Hugging Face
  nicht erreichbar war; jetzt meldet er den Fehler. Eine Lizenz gilt nur auf einer festen Positivliste als
  freizügig: `other`, eigene, listenförmige und unbekannte Lizenzen werden als unbekannt gezeigt, und der Browser
  fragt vor dem Download eines solchen Modells nach. Eine Datei, die der Browser herunterlädt, kommt aus dem Commit,
  den er zuerst ermittelt hat, nicht aus einem Branch, der sich inzwischen bewegen kann.
- Rückfragen des 2026-07-28-Protokolls werden je Aufrufer und Sitzung geführt (die Antwort eines Clients schließt die
  gleiche Rückfrage eines anderen Clients nicht mehr), eine unbeantwortete wird von einem Timer als `unanswered`
  festgehalten, auch wenn keine weitere Anfrage kommt, und die beim Ende einer Sitzung noch offenen als
  `client_disconnected`; die Berechtigungsprüfung eines Long-Polls liest die Berechtigungsdatei nicht mehr in der
  Ereignisschleife des Servers; jede Ablehnung mit dem Grund `failed` nennt jetzt einen nächsten Schritt, und die
  Eingabe-Ablehnungen des Build-Service tragen ihren eigenen Grundcode; eine MCP-Bestätigung, die scheiterte oder
  nicht rechtzeitig beantwortet wurde, wird mit einem Grundcode festgehalten (`failed`, `needs_person`), und ihre
  Ablehnung nennt einen nächsten Schritt.
- Zweistufige Bestätigung, auf beiden Wegen gleich: Eine MCP-Rückfrage, die für ein Formular zu lang ist, wird mit
  dem Grund `limit_reached` und der Antwort `unavailable` festgehalten und nennt einen nächsten Schritt, wie in
  Dittos Fenstern; eine Rückfrage, die ein nachgeschlagener Fakt nötig gemacht hat, wird mit der Entscheidung `ask`
  festgehalten (bisher `allow`); `resolve_healing` entscheidet nichts ohne die Fakten seiner Rückfrage, und die erste
  Zeile seiner Rückfrage nennt den Build, nicht nur seine ID; ein lokales Modell oder Dataset, das eine KI benannt hat
  und das vor dem Build verschwunden ist, wird in der Ablehnung bei seinem Label genannt, nie bei seinem echten Pfad.
- Grenzen, auf beiden Wegen gleich: Dittos vorzeitiges Aufhören sieht, um welches Feld es bei einem Eingabefehler
  geht (die Registry ließ es weg, sodass ein Modell, das ein Feld nach dem anderen behob, gestoppt und sein richtiger
  Aufruf nicht ausgeführt wurde); eine Ablehnung der Rückfrage-Grenzen (nach einem Nein, ein pausiertes Tool, zu
  viele Rückfragen) ist auch auf Dittos Weg kein gescheiterter Aufruf (dort wurde daraus "Stop retrying", während ein
  MCP-Client die einfache Ablehnung bekam); ein verweigerter Start, etwa wegen einer vollen Warteschlange, verbraucht
  keinen der 20 Builds am Tag mehr; nach dem Stopp-Hinweis bekommt ein Modell, das weiter Tools aufruft, höchstens
  zwei weitere Runden statt des restlichen Budgets.
- Modul-Assistent: Ein Basis-Image, das Ditto vorgeschlagen hatte, wurde durch `debian:bookworm-slim` oder
  `ubuntu:22.04` ersetzt, wenn sein Name nur „debian“ oder „ubuntu“ enthielt (zum Beispiel ein Hersteller-Image auf
  `nvcr.io`), sodass ein anderes Image als das vorgeschlagene gebaut worden wäre. Das vorgeschlagene Image bleibt jetzt
  unverändert und wird mit seiner Registry gezeigt, und die Meldungen mit dem Text der KI (auch Dittos Fehler und
  Absagen) erscheinen als reiner Text statt als Rich Text.
- MCP-Server: Auf Protokoll 2026-07-28 kündigte er Änderungsmeldungen für Tools, Prompts und Ressourcen sowie
  Ressourcen-Abonnements an, schickte aber nie eine. Jetzt kündigt er sie nur zusammen mit den Ereignissen an, die er
  veröffentlicht, und ohne Ereignisse wird er mit abgeschalteten Abonnements gebaut.
- MCP-Server: Ein Client, der die Verbindung trennt, lässt seine Builds nicht mehr ohne jemanden laufen, der
  aufräumt. Die Builds und Jobs, die seine Sitzung gestartet hat, werden abgebrochen, jeweils mit einem Audit-Eintrag
  („client disconnected“), der Server wartet bis zu 30 Sekunden, bis ihre Container gestoppt sind, und
  `llm-cli mcp serve` fährt danach das Framework herunter. Ein SIGTERM des Clients (so beenden Clients einen
  stdio-Server, der nach dem Schließen seiner Eingabe nicht endet) beendet den Prozess nicht mehr vor diesem
  Aufräumen.
- Secrets-Speicher: Ein Speicher einer früheren Version, der sich nicht entschlüsseln lässt, wird von
  `llm-cli doctor` gemeldet, statt unlesbar in den Speicher im Benutzerordner übernommen zu werden.
- Lesen oder Speichern eines Geheimnisses scheitert nicht mehr in einem Container, dessen Benutzer keinen Namen hat
  (keine Variable `USER` und kein Eintrag in passwd).
- Unter Windows scheitert das Speichern eines Geheimnisses nicht mehr, wenn ein anderer Prozess oder ein
  Virenscanner den Speicher kurz geöffnet hat.
- `llm-cli secrets set` sagt, wohin das Geheimnis ging (in den verschlüsselten Tresor oder mit `ephemeral` nur in
  den Arbeitsspeicher), statt immer „Keyring“. Auch die GUI nennt den verschlüsselten Tresor für Zugangsdaten (der
  Dialog für Geheimnisse, das Feld für den API-Schlüssel und seine Fehlermeldung in den KI-Einstellungen, das
  Kontrollkästchen für das Token im Hugging-Face-Browser).
- Die CLI und die CLI-Anleitungen raten nicht mehr dazu, das GitHub-Token auf der Kommandozeile zu übergeben, wo
  der Shell-Verlauf es behält (diese Form funktionierte auch nicht). `llm-cli secrets set <name>` fragt den Wert
  verdeckt ab.
- Der Deep-Ingest-Dialog öffnet sich mit den Einstellungen `crawler_max_depth`, `crawler_download_pdfs` und
  `crawler_rate_limit` (Tiefe 0 und Pausen unter einer Sekunde möglich, größere Werte bleiben); vorher setzten
  sich seine festen Werte (2, PDFs an, 1 s) immer über sie hinweg.
- `llm-cli --help` und die Beschreibung der CLI nannten Version 2.4.0; jede Versionsangabe kommt jetzt aus der
  Paketversion.
- `llm-cli build status` listet Builds, die die GUI, der MCP-Server oder ein anderer CLI-Aufruf gestartet hat: Jede
  Build-Engine schreibt je Build einen Eintrag nach `<cache_dir>/build_records/` (Status, Stufe, Fortschritt, der
  Fehler geschwärzt), und ein Build, dessen Programm endete, bevor er fertig war, erscheint als unterbrochen. Der
  Befehl braucht weder den Tresor für Zugangsdaten noch Docker; `--all` listet jeden aufbewahrten Build statt der
  20 neuesten. Unter Linux und macOS sind die Einträge nur für den Benutzer lesbar, der gebaut hat (Ordner 0700,
  Dateien 0600).
- Strg+C in `llm-cli build start` bricht den Build über den Build-Service ab: Sein Container wird gestoppt, die CLI
  wartet, bis der Build angehalten hat (auch wenn er gerade noch an die Build-Engine übergeben wurde), sagt das
  und endet mit Exit-Code 130. Vorher liefen Build und Container weiter.
- `llm-cli scan --output DATEI` erreicht die Hardware-Probe (`hardware_probe.sh --output`,
  `hardware_probe.ps1 -OutputFile`), die immer `target_hardware_config.txt` in den aktuellen Ordner schrieb. Unter
  Windows ohne Administratorrechte wartet die CLI auf den Neustart der Probe mit erhöhten Rechten, statt sofort zu
  melden, dass keine Datei geschrieben wurde.
- `llm-cli module create` schreibt in den eingestellten Targets-Ordner (`targets_dir`, relativ zur Installation)
  statt nach `targets/` im aktuellen Ordner; seine nächsten Schritte nennen `modules/build.sh`, das Skript, das
  Builds ausführen, statt der Altlast `config_module.sh`.
- `llm-cli config show` gibt die wirksamen Einstellungen aus (`config.yml`, eine Datei aus `-c`, Variablen
  `LLM_FRAMEWORK_*`, Vorgaben), jeweils mit ihrer Herkunft und mit geschwärzten Zugangsdaten, statt der
  eingebauten Vorgaben der CLI.
- `llm-cli config repos --add` speichert den Eintrag `{url: ...}`, den die Build-Engine als `<NAME>_REPO_OVERRIDE`
  weiterreicht; reine URLs aus früheren Versionen werden ebenfalls weitergereicht. Der Name kommt aus dem ganzen
  Eintragsnamen (`llama.cpp` und `llama_cpp` ergeben beide `LLAMA_CPP_REPO_OVERRIDE`, den Namen, den die
  Target-Skripte lesen; `llama.cpp` ergab `CPP_REPO_OVERRIDE`). `--add` und `--remove` enden mit Exit-Code 1,
  wenn sich die `config.yml` nicht speichern lässt, statt Erfolg zu melden; `--add` lehnt einen Namen ohne
  ASCII-Buchstaben und Ziffern ab (Exit-Code 2).
- Eine mit `-c` angegebene Einstellungsdatei, die sich nicht lesen lässt, verwirft nicht mehr auch die Variablen
  `LLM_FRAMEWORK_*`: Sie wird auf stderr genannt und übergangen, und die übrigen Einstellungen gelten.
- Der Hardware-Scan dieses PCs in der GUI (**Scan / Import...**) schreibt sein Ergebnis nach `<cache_dir>/probes/`
  statt in den Arbeitsordner der GUI, und *Auto-fill from Probe Result* im Modul-Assistenten liest es dort.
- Der SSH-Scan eines Boards in der GUI (**Scan / Import...**) scheiterte auf Boards, die `/tmp` mit `noexec`
  einhängen: Er kopierte `hardware_probe.sh` dorthin und führte es dort aus. Jetzt schickt er die Probe über den
  SSH-Kanal wie `llm-cli scan --device` (die Python-Probe ab Python 3.8, sonst die Shell-Probe), schreibt nichts auf
  das Board und legt das Profil unter der ID des Boards ab.
- Das Deployment eines Modellpakets auf ein Board scheiterte, wo `/tmp` mit `noexec` eingehängt ist (das Paket wird
  dort entpackt, und `deploy.sh` wurde von dort ausgeführt), und auf Boards ohne bash (das Skript begann mit
  `#!/bin/bash`). `deploy.sh` ist jetzt ein reines POSIX-`sh`-Skript und wird mit `sh deploy.sh` gestartet.
- Das Deployment aus der GUI schreibt das Paket in den eingestellten Ausgabeordner (`output_dir`, relativ zur
  Installation) statt nach `output/` im Arbeitsordner.
- Der Dialog der KI-Einstellungen warnt, wenn sich die Einstellungen nicht in der `config.yml` speichern ließen
  (sie gelten bis zum Programmende); vorher stand das nur im Protokoll.
- Windows: Der Uninstaller (`scripts/Uninstall-LLM-Conversion-Framework.bat`) findet die Installation.
  `install.bat` schreibt jetzt die Markierungsdatei mit dem Installationsordner, die der Uninstaller liest, neben
  `install.bat`, wo der Uninstaller sie sucht; vorher schrieb kein Installer sie, und der Uninstaller meldete immer,
  es sei nichts installiert. Meldet PowerShell keinen Skriptpfad (der PowerShell-Teil von `install.bat` läuft über
  `Invoke-Expression`), nimmt `install.bat` den aktuellen Ordner, den sein Batch-Teil auf den eigenen gesetzt hat.
  Das Startskript
  `scripts/start-llm_convertion_framework.bat` funktioniert auch aus `scripts\` (es suchte `orchestrator\main.py`
  und `.venv` dort).
- `llm-cli targets list` zeigt für jedes Target die ID (den Wert für `--target`), den Namen sowie Boards und
  Formate aus seinen Capabilities statt Platzhaltern; `llm-cli targets validate` prüft den Block `capabilities:`
  gegen das Schema und gibt jeden Fehler aus (Exit-Code 1).
- `llm-cli deploy package`, `secrets set` und `scan` enden mit Exit-Code 1, wenn sie scheitern (vorher mit 0).
- Die Hilfe zu `llm-cli doctor --tier` nennt nur Korrekturen, die es gibt (Stufe 1 macht den persönlichen Ordner
  privat; eine Korrektur für Ordner gibt es nicht).
- Ein Target, dessen `metadata.id` vom Ordnernamen abweicht, lässt sich bauen: CLI, Build-Service, Orchestrator und
  Build-Engine lösen Targets über eine einzige Zuordnung von ID zu Ordner auf (vorher übersprang der Orchestrator
  ein solches Target mit „Nothing to build“), und `-t` nimmt die ID oder den Ordnernamen.
- Die Einstellung `max_concurrent_builds` legt die Zahl der Build-Slots fest (sie hatte keine Wirkung; die Engine
  nutzte immer 2).
- Audit-Log: Schrieben mehrere Prozesse (GUI, CLI, MCP-Server) gleichzeitig die ersten Einträge eines neuen Logs,
  konnte das Log als beschädigt gemeldet werden („The audit anchor file was missing when entry 2 was written“).
  Der Anker eines neuen Logs wird jetzt geschrieben, bevor sein erster Eintrag für andere Prozesse sichtbar ist.
- CLI, GUI und MCP-Server legen nicht mehr bei jedem Start leere Ordner `cache/`, `models/` und `output/` in der
  Installation an, auch nicht, wenn `LLM_FRAMEWORK_CONFIG_DIR` oder `LLM_FRAMEWORK_HOME` woanders hinzeigen; ein
  Build oder ein Download legt die Ordner an, die er nutzt. Die `.gitignore` des Repositorys erfasst `output/`,
  `cache/`, `models/` und `logs/`, damit Build-Ergebnisse und Downloads nicht committet werden können.
- Die beim ersten Start der GUI gewählte Sprache wird im tatsächlich genutzten Einstellungsordner gespeichert
  (`LLM_FRAMEWORK_CONFIG_DIR`, das `configs/` der Installation, wenn beschreibbar, sonst der Benutzerordner) statt
  immer im `configs/` der Installation.
- Das Speichern einer Einstellung (zum Beispiel der Sprache beim ersten Start oder der KI-Einstellungen)
  überschreibt eine `config.yml`, die sich nicht lesen lässt (ein YAML-Fehler oder keine Einstellungen der Form
  `schlüssel: wert`), nicht mehr mit den geänderten Werten allein; die Datei bleibt, wie sie ist, und der Fehler
  wird protokolliert.
- Eine Zahl in `config.yml`, die nicht zu ihrer Einstellung passt (`.inf` für eine ganze Zahl oder `.inf`/`.nan`
  für eine Dezimalzahl), wird mit einer Warnung übergangen, und der Standardwert gilt; vorher brach `.inf` für eine
  ganze Zahl das Lesen der Datei ab, sodass auch jede Einstellung danach übergangen wurde.
- Windows: Liegt das Framework in `C:\Program Files`, wo normale Benutzer nicht schreiben dürfen, liegen die
  Einstellungen und die Datenordner `output/`, `cache/` und `models/` im Benutzerordner
  (`%USERPROFILE%\.llm-framework`). Die Prüfung auf Schreibrechte verlässt sich nicht mehr auf `os.access`, das
  unter Windows die Ordnerrechte nicht beachtet. Der Installer legt keinen ungenutzten Datenordner im öffentlichen
  Dokumente-Ordner mit Desktop-Verknüpfung mehr an.
- `install.sh` prüft die Verbindung per HTTPS zum Paketindex (`pypi.org` oder `PIP_INDEX_URL`) statt mit einem Ping
  an 8.8.8.8, den viele Netze sperren. Er lädt dazu eine kleine Projektseite statt des ganzen Index, zeigt
  Zugangsdaten in `PIP_INDEX_URL` nicht an und bricht mit einer klaren Meldung ab, wenn er mit `sh` statt bash
  gestartet wird. Er legt keinen ungenutzten Datenordner im Home-Ordner von root mehr an. Ein erneuter Aufruf
  entfernt nur Dateien, die ein früherer Aufruf kopiert hat (aufgeführt in `.lcf-installed-files`) und die das
  Repository nicht mehr ausliefert; vorher löschte sein `rsync --delete` alles in `/opt`, was das Repository nicht
  ausliefert: mit dem Modul-Assistenten oder `llm-cli module create` erstellte Targets, installierte Community-Packs
  sowie Builds, Cache, Modelle und `configs/config.yml`. Lässt sich die Liste nicht erstellen, bricht er ab, bevor
  etwas kopiert oder entfernt wird; ein veralteter Link wird selbst entfernt, nie die Datei, auf die er zeigt; und
  die Kopie von `install.sh` in `/opt` kopiert und entfernt nichts.
- Deployment-Pakete übernehmen Benutzerprofile, Sprachvorlagen und Prompts nur aus dem Einstellungsordner, nie aus
  einem Ordner `config/` im Arbeitsverzeichnis.
- Der Hugging-Face-Modellbrowser lässt sich wieder öffnen: **Tools > Hugging Face Model Browser...** (kein Menü
  führte zu ihm). Seine Lizenzprüfung friert das Fenster nicht mehr ein, und Enter während einer Suche oder ein
  Doppelklick auf ein anderes Modell während eines Downloads bringt die GUI nicht mehr zum Absturz.
- `scripts/sync_ssot.py` nutzt die echte Datei `configs/detailed_project_sources.yml` (es legte stattdessen eine
  Kopie namens `detailed_project.sources.yml` an) und funktioniert aus jedem Ordner. Es listet die Unterschiede auf
  und ändert mit `--write` nur diese Werte; Kommentare und Layout bleiben (es schrieb die ganze Datei mit
  `yaml.dump` neu).
- Der Kopfkommentar von `configs/project_sources.yml` sagt, wer die Datei liest (kein Teil des Frameworks im
  Betrieb; die beiden SSOT-Skripte), und ihr veralteter Roadmap-Abschnitt (v2.2 bis v2.4) ist entfernt.
- Erzeugte Target-Module (Modul-Assistent, `llm-cli module create`, `llm-cli module generate-ai`): Das
  Vorlagen-`build.sh` folgt dem Rockchip-Dispatcher. Es nutzt `convert_hf_to_gguf.py` und `build/bin/llama-quantize`
  von llama.cpp, schreibt die F16-Zwischendatei in den Arbeitsordner statt nach `/tmp` (in der Sandbox ein
  1-GB-tmpfs) und bricht mit einem Fehler ab, statt das Modell unverändert zu kopieren. Sein Paketierschritt kopiert
  keinen Ordner `build/` mehr, den es nicht gibt, was jeden Build eines erzeugten Moduls am Ende scheitern ließ.
- Secret-Scan mit dem Werkzeug pre-commit: Seine pre-push-Stufe durchsucht die Commits, die gepusht werden
  (`--push`, das den gepushten Commit jetzt aus `PRE_COMMIT_TO_REF` liest), wie `.githooks/pre-push`, statt der
  ganzen Historie. Die Prüfung vor dem Commit (`--staged`) durchsucht auch eine Datei, die einen symbolischen Link
  ersetzt hat, und Dateien, deren Name einen Zeilenumbruch enthält oder wie `0:` beginnt; die übersprang sie vorher.
- `hardware_probe.ps1` fragte Windows nach den falschen CPU-Merkmalen: Konstante 40 (AVX2) als AVX-512 und 37
  (SSE4.1) als NEON, sodass fast jeder PC AVX-512 und NEON meldete. Jetzt fragt es die dokumentierten Konstanten (AVX
  39, AVX2 40, AVX-512 F 41, BMI2 60; auf ARM NEON 29, Skalarprodukt 43, i8mm 66, FP16 67, SVE 46, SME 70) und schreibt
  `UNKNOWN`, wo Windows es nicht sagen kann. Außerdem weist es der schreibgeschützten Variable `$PID` keinen Wert mehr
  zu, was die Probe abbrach, sobald ein Rockchip-Board per USB angeschlossen war.
- `hardware_probe.sh` sah weder das ARM-Skalarprodukt noch die FP16-Arithmetik, schrieb das x86-Flag `f16c` auf ARM
  als FP16-Unterstützung, erkannte nur RK3588 und RK3566 und behielt nur die erste GPU; jetzt schreibt es die
  ARM-Merkmale, die Kerncluster, jeden SoC aus dem Device Tree und alle GPUs.
- **Unbeantwortete, abgelaufene und abgebrochene Bestätigungen werden protokolliert** (MCP-Server): Eine Frage
  während des Aufrufs (klassisches Protokoll) wartet höchstens 5 Minuten und wird dann als `confirmation_timeout`
  festgehalten, eine nie beantwortete Frage des Protokolls 2026-07-28 nach 15 Minuten als `unanswered`, und ein
  Aufruf, den der Client abbricht, auch während seine Frage offen ist, als `cancelled`. Bisher endeten diese ohne
  Audit-Eintrag, und eine Frage konnte unbegrenzt offen bleiben.
- **Der Abbruch eines Clients erreicht einen laufenden Aufruf sofort** (MCP-Server): Der Aufruf wird als abgebrochen
  festgehalten und der laufende Handler sofort benachrichtigt; was schon läuft, endet, und sein Abschlusseintrag
  folgt. Bisher kam der Abbruch erst an, nachdem der Handler fertig war, und nichts wurde festgehalten.
- **Unbekannte Argumente werden über MCP nicht mehr weggelassen**: `quant` statt `quantization` baute die
  Standard-Quantisierung des Targets und meldete Erfolg, während Dittos Weg denselben Aufruf ablehnte. Beide Wege
  lassen jetzt denselben Normalisierer über die rohen Argumente laufen, MCP-Clients bekommen die geschlossenen
  Eingabeschemas der Tools (`additionalProperties: false`) statt offener, JSON-Text in einem Listenfeld wird auf
  beiden Wegen angenommen, und unbekannte Tool-Namen werden im Audit-Log festgehalten (bisher auf keinem Weg).
- **Die Tests berühren den echten Benutzerordner nicht mehr.** Ein Testlauf hatte dort SSH- und Hugging-Face-Dateien
  angelegt (`~/.ssh`, `~/.cache/huggingface`). Bevor irgendetwas das Framework importiert, lenkt `tests/conftest.py`
  jetzt `HOME`, `USERPROFILE`, die `XDG_*`-Ordner, `HF_HOME`, `HF_TOKEN_PATH` und die übrigen Variablen für
  Benutzerordner in einen temporären Ordner um (jeder Test bekommt seinen eigenen), entfernt Zugangsdaten wie
  `HF_TOKEN` und `SSH_AUTH_SOCK` sowie den Sitzungsbus des Desktops aus der Umgebung, gibt jedem Test eine
  Schlüsselbund-Datei in seinem eigenen Benutzerordner statt des Schlüsselbunds des Betriebssystems (Secret Service,
  macOS-Schlüsselbund, Windows-Anmeldeinformationsverwaltung) und lässt den Lauf scheitern, wenn sich die Schlüssel-,
  Token-, Schlüsselbund- oder Framework-Ordner des echten Benutzerordners verändert haben (er vergleicht ihre
  Auflistungen und öffnet nie eine Datei).
- **Ausstehende Freigaben zeigen die zweiphasige Rückfrage und laufen gebunden an sie.** Ein Aufruf, der im
  Framework auf dich wartet, zeigt dieselbe Rückfrage wie jedes andere Fenster: die Fakten, die das Framework
  nachgeschlagen hat, die sprechenden Namen seiner IDs und wer fragt. Wenn du ihn freigibst, werden die Fakten erneut
  nachgeschlagen; würde die Rückfrage anders lauten (etwa ein neuer Upstream-Commit), wirst du erneut gefragt, sonst
  läuft der Aufruf genau mit den Fakten, die du gesehen hast. Eine MCP-Rückfrage zwischen 4.000 und 16.000 Zeichen
  wird jetzt zu einer ausstehenden Freigabe statt abgelehnt, eine Dauerfreigabe wird auch auf dem MCP-Weg an den
  nachgeschlagenen Fakten gemessen (ein Client, der fragen könnte, fragt nicht, wenn eine den Aufruf abdeckt), Dittos
  scrollbarer Dialog für lange Rückfragen bietet ebenfalls "nicht mehr fragen" an, und die Kontingente eines
  freigegebenen Aufrufs werden vor dem Ausführen noch einmal geprüft.
- **Target-Namen, die sich gleich lesen**: Ein neues Target (Modulgenerator, Assistent, `create_target_module`), dessen
  Name ohne Rücksicht auf Groß- und Kleinschreibung und Trennzeichen der ID oder dem Ordner eines vorhandenen Targets
  gleicht (`rockchip`, `Rock-Chip` neben `Rockchip`), wird abgelehnt, damit es nie den Namen eines anderen Targets
  übernimmt.
- **Ein Modul auf der GPU-Vorlage baut das Basis-Image, das es nennt**: Ein Modul auf der GPU-Vorlage mit einem
  anderen Basis-Image als dem Standard (zum Beispiel einem Toolchain-Image eines Herstellers) behielt still die
  Ubuntu-Basis der Vorlage; jetzt beginnen beide Stufen damit, und der Generator prüft, dass die Builder-Stufe mit dem
  Basis-Image des Moduls beginnt. Image-Angaben mit Registry-Port (`registry.example:5000/img@sha256:...`) werden
  angenommen.

- **`start_build` über MCP ignorierte die Optimierungsstufe** (immer balanced, G26), und **zwei Builds verschiedener
  Commits eines Modells teilten sich einen Bereitstellungsordner** (und `a/b__c` und `a__b/c` kollidierten, G38).
- **Ein volles Laufwerk schlug immer vor, Docker aufzuräumen** (G40): Der Vorschlag ist jetzt ein manueller Schritt,
  der die Ordner des Frameworks auf dem vollen Laufwerk mit ihrer Größe nennt; Docker aufzuräumen wird nur
  vorgeschlagen, wenn Dockers Datenverzeichnis voll ist.

### Sicherheit
- **`run_benchmark` startet genau die Datei, die es gehasht hat**: das Modell wird einmal geöffnet, über die offene
  Datei gehasht und llama-bench als genau diese offene Datei übergeben (nativ ein geerbter Deskriptor, in der
  Sandbox ein privater Hardlink), so dass ein Katalogmodell zwischen Prüfung und einem nativen Lauf ohne Zustimmung
  nicht gegen ein nicht vertrauenswürdiges ausgetauscht werden kann.
- **Fehler der Board- und lokalen Laufzeit-Tools nennen keinen Pfad des Rechners**: Pfade in ihren Meldungen
  erscheinen als Ordner-Bezeichnungen des Frameworks (`state:...`, `cache:...`, `output:...`, `models:...`) oder nur
  als Dateiname, nie als Pfad des Rechners oder `~/...`.
- **robots.txt lässt sich nicht mehr über Crawl-Optionen abschalten**: Der Crawler nimmt nur seine eigenen
  Optionsschlüssel (Seiten, Tiefe, Pause, PDFs, Sprachen) und übergeht jeden anderen Schlüssel, auch
  `respect_robots`, mit einer Log-Zeile; der Schalter für Tests mit einem nachgebildeten Web ist ein eigener
  Schlüsselwort-Parameter, den kein Produktcode übergibt (ein Wächtertest prüft das). Zeichenketten im Ergebnis eines
  Jobs (etwa die Adressen, die ein Crawl übersprungen hat, und die Gründe der Websites) werden jetzt wie der Rest des
  Job-Status als externer Text bereinigt, gekürzt und markiert.
- **Fehler von Geräten und Benchmarks tragen keinen fremden Text und keine Pfade des Rechners mehr in den Kontext
  einer KI**: Scheiterte ein Job von `probe_remote_device`, `deploy_device_runtime`, `run_device_benchmark` oder
  `run_benchmark`, enthielt die Fehlermeldung unmarkiert die Fehlerausgabe des Geräts oder die letzte Ausgabe von
  `llama-bench` (das die Metadaten des geladenen GGUF ausgibt, Text, den ein nicht vertrauenswürdiges Modell
  bestimmt); ein gescheitertes mktemp auf dem Gerät setzte dessen Fehlerausgabe in das schlichte `reason` von
  `deploy_to_device`; und ein Fehler beim Paketieren oder Deployen konnte den Build-Ordner, den Ausgabeordner oder den
  Home-Ordner nennen. Meldung und `reason` sind jetzt die eigenen Sätze des Frameworks ohne Pfade des Rechners, und
  was das Gerät oder das Programm ausgegeben hat, kommt in `get_job_status.external_text` zurück, als externer Text
  markiert (auch bei gescheiterten Jobs).
- **Dittos MCP-Client-Modus läuft nicht mehr als gemeinsamer lokaler Aufrufer, wenn das Token den Server nicht
  erreicht** (MCP-Entwurf T12): `docker exec` und `podman exec` geben ihre Umgebung nicht in den Container weiter,
  darum startete das dokumentierte `docker exec -i <Container> llm-cli mcp serve` den Server dort ohne
  `LCF_MCP_TOKEN`, als Aufrufer `mcp` mit anderen Berechtigungen als Dittos. Ditto ergänzt bei einem solchen Befehl
  jetzt `-e LCF_MCP_TOKEN` (nur den Namen, nie den Wert) und lehnt einen stdio-Server ab, dessen `get_status` einen
  anderen Aufrufer als sein Token nennt (zum Beispiel über `ssh`, das die Umgebung verwirft), mit dem nächsten
  Schritt.
- **Ein abgebrochener Job von `restore_knowledge_snapshot` ersetzt die Wissensbasis nicht mehr** (MCP-Entwurf T10):
  Ein Abbruch (`cancel_job` oder das Ende der Sitzung) markierte den Job sofort als abgebrochen, während die
  Wiederherstellung weiterlief. Sie prüft den Abbruch jetzt nach der Sicherung und bevor die Wissensbasis ersetzt
  wird, und lässt sie dann, wie sie war.
- **Ein lokaler Modellordner mit Pickle-Gewichten fällt nicht mehr unter einen Dauerauftrag für Builds**
  (Entwurf 6.10): Pickle-Dateien neben einer safetensors-Datei (der ganze Ordner wird in den Build eingebunden)
  zählten als safetensors. Pickle-Gewichte irgendwo im Ordner machen ihn jetzt zu „pickle“, also wird die Person
  gefragt.
- **Jedes Basismodell eines zusammengeführten Modells wird auf Zugangsbeschränkung geprüft** (Entwurf 6.10): Nannte
  eine Modellkarte mehrere Basismodelle, wurde nur das erste nachgeschlagen, sodass ein beschränktes zweites
  Basismodell den Build oder Download nicht aus einem Dauerauftrag heraushielt. Jetzt wird jedes nachgeschlagen; ein
  beschränktes oder nicht nachschlagbares hält ihn heraus.
- **Der Filter für gefährliche Befehle lässt sich nicht mehr mit unsichtbaren Zeichen oder Zeichen in voller
  Breite umgehen** (Healing-Analysen und Empfehlungen von Ditto sowie Korrekturvorschläge von KI-Clients): Er
  prüft den Text auch so, wie GUI und CLI ihn zeigen (normalisiert, unsichtbare Zeichen und Verbinder entfernt),
  sodass `r<Leerzeichen ohne Breite>m -rf ~` oder Buchstaben in voller Breite nicht mehr durchkommen und dann als
  lesbarer Befehl erscheinen. Der Vorschlag eines Clients wird in dieser bereinigten Form aufbewahrt; der Hinweis
  in der Statusleiste der GUI nennt den Build bei seinem sprechenden Namen und zeigt den Namen des Clients
  bereinigt.
- **Die Abfrage des Basis-Images eines neuen Target-Moduls bleibt bei öffentlichen Adressen**: Eine Registry (jeder
  öffentliche Host, den eine KI in `base_os` nennt) konnte die anonyme Digest-Abfrage oder ihre Token-Anfrage per
  Weiterleitung ins lokale Netz umlenken, weil Weiterleitungen ungeprüft gefolgt wurde. Jede Weiterleitung wird jetzt
  von Hand verfolgt und muss wie die erste Adresse https und öffentlich sein, Verbindungen gehen nur zur geprüften
  Adresse, und das Pull-Token einer Registry geht nie an einen anderen Host.
- **`create_target_module` plant den `runtime:`-Block aus genau der Probe, die der Person gezeigt wurde**: Eine Probe,
  die zwischen Frage und Schreiben ersetzt wurde (zum Beispiel `local` neu gemessen, während eine ausstehende Freigabe
  wartete), stoppt das Schreiben, statt verwendet zu werden.
- **Ein fehlgeschlagener `probe_local_hardware`-Auftrag nennt keinen Host-Pfad**: Konnte die Probe nicht gespeichert
  werden, enthielt der Fehler des Auftrags (den der KI-Client liest) den vollen Pfad des Benutzerordners; jetzt nennt
  er nur den Grund, und `llm-cli scan` zeigt den vollen Fehler weiterhin im Terminal.
- **Dittos Modul-Weg folgt derselben Regel für Basis-Images wie das MCP-Tool**: `llm-cli module generate-ai` lehnt ein
  Basis-Image, das kein Docker Official Image ist, ohne `base_image_reason` der KI ab, wie `create_target_module`.
  Die Anzahl der Herstellerquellen, pip-Pakete und Downloads hat nirgends mehr eine harte Grenze (über 20, 200 und 50
  wurden sie abgelehnt); es bleiben nur die weichen Grenzen mit ihrer Warnung.
- **KI-Clients sehen nie den Host-Pfad eines lokalen Modells, das sie benannt haben** (MCP-Clients und Ditto): Der
  Stufentext des Builds, der Kontext der Selbstheilung, `model_source` im Build-Manifest und `list_artifacts` nennen
  es bei seinem `<label>:<relativer Pfad>` (den Host-Pfad eines älteren Manifests beim Label, sonst nur beim
  Dateinamen).
- **Ein MCP-Client kann den Eintrag und die Grenzen einer Rückfrage nicht mehr umgehen, indem er seinem Aufruf eine
  Antwort mitgibt** (2026-07-28-Runden): Eine Runde zählt nur als Antwort auf eine Rückfrage, die das Framework genau
  diesem Client gestellt hat und die noch offen ist. Bisher wurde jeder Aufruf, der irgendeine Antwort mitbrachte,
  ohne den vorab geschriebenen Eintrag `confirmation_requested` gefragt und ohne die 10 Minuten Wartezeit nach einem
  Nein, die Pause nach drei Nein und die Zählung der Rückfragen pro Stunde.
- Dittos Build-Empfehlung und der Prompt `recommend_build_settings` geben dem Modell die Formate,
  Quantisierungstypen und Format-Hinweise des Targets (Text aus `target.yml`, den ein Community-Target-Pack
  installiert) in einem nicht vertrauenswürdigen Block; vorher standen sie als einfacher Text neben den Anweisungen.
- `LCF_MCP_TOKEN` nimmt `llm-cli` selbst aus der Umgebung, bevor irgendein Befehl läuft, und gibt es nur an
  `mcp serve` weiter; vorher entfernte es nur `mcp serve`, sodass jeder andere Befehl und seine Kindprozesse es erbten.
- MCP-Server: Was außerhalb einer vom Server geprüften Anfrage läuft, bekommt den Aufrufer ohne jede Berechtigung,
  auch auf einem stdio-Server, der vorher auf seinen eigenen Aufrufer zurückfiel.
- Der MCP-Token-Speicher wird anhand des SHA-256 der Dateibytes zwischengespeichert statt anhand von Identität, Größe
  und Änderungszeit der Datei, sodass ein laufender Server ein widerrufenes oder rotiertes Token nach einer Änderung
  gleicher Größe innerhalb der Auflösung des Zeitstempels nicht weiter annehmen kann.
- Ditto verwirft einen Healing-Fix oder eine Build-Empfehlung, die einen gefährlichen Befehl enthält (einen
  Download, der direkt in eine Shell geleitet wird, das Löschen des Wurzel- oder Home-Ordners, Schreibrechte für
  alle, das Überschreiben eines Datenträgers), ganz gleich, welche Anweisungen ein Build-Log oder ein
  Modellname dem Modell gegeben hat.
- Anfragen an lokale Modellserver auf 127.0.0.1 laufen nie über einen HTTP(S)-Proxy aus der Umgebung.
- Downloads lokaler Modelle werden abgelehnt, wenn Größe und sha256 nicht gepinnt sind; das offizielle vorgebaute
  Archiv mit dem Server-Programm (nur mit `runtime install --prebuilt`) wird genauso geprüft, atomar installiert und
  nie auf musl oder einer älteren glibc angeboten als der, gegen die es gebaut wurde.
- Eine Startregel für jedes llama.cpp-Programm, das das Framework startet (`orchestrator/runtime/server_policy.py`):
  Dittos Server, die Prüfungen nach dem Build, die Perplexity-Prüfung, das Startskript und die systemd-Unit auf einem
  Board und die Prüfungen dort teilen eine Liste verbotener Optionen (Tools, Agentenmodus, MCP, Weboberfläche,
  entferntes Rechnen, Downloads, ein Prompt-Protokoll auf der Platte, ein Schlüssel oder Token auf der Befehlszeile,
  in jeder Schreibweise, die llama.cpp annimmt, auch `--a_b` für `--a-b`) und eine geleerte Umgebung, und jeder
  Server lauscht nur auf 127.0.0.1 und liest seinen Schlüssel aus einer privaten Datei, gebunden an dieselben
  Server-Optionen (Dittos Befehlszeile wird unmittelbar vor dem Start noch einmal geprüft, die des Startskripts, wenn
  es erzeugt wird). `runtime start` startet nur einen Build, der seine Prüfung auf diesem
  Rechner bestanden hat, noch zu seinem Manifest passt und vom gepinnten Commit ist (sonst seinen CPU-Ersatz, sonst
  nichts); ein selbst gebauter Build, der nicht genutzt werden kann, weicht nie von selbst auf das vorgebaute Archiv
  aus, und ein offizielles Archiv, das eine frühere Version heruntergeladen hat, wird erst nach
  `runtime install --prebuilt` genutzt (das es übernimmt, ohne es neu herunterzuladen).
- Deep Ingest liest robots.txt so, wie RFC 9309 es beschreibt, mit einer vorsichtigen Abweichung. Regeln mit `*`
  oder `$` (etwa `Disallow: /*.pdf$`), ein `Allow` vor einem längeren `Disallow`, jede Gruppe `User-agent: *` nach
  der ersten und eine Datei, die mit einer Byte Order Mark beginnt, wurden bisher ignoriert, sodass der Crawler
  Seiten abrief, die die Site ausgeschlossen hatte. Seine Gruppen werden am genauen Produkt-Token
  `LLM-Conversion-Framework-Bot` erkannt (gleich in welcher Schreibweise), sodass eine Gruppe für `bot` nicht mehr
  über `*` siegt, und jedes Schema, jeder Host und jeder Port hat eine eigene robots.txt. Die Abweichung: Eine
  robots.txt, die mit 401 oder 403 antwortet, heißt „alles verboten“, wo RFC 9309 das Crawlen erlauben würde; ein
  Vorbehalt, den der Crawler nicht lesen kann, zählt trotzdem.
- Deep Ingest liest jede Antwort bis zu einer Grenze (HTML-Seiten 8 MiB, PDFs 64 MiB, robots.txt 500 KiB,
  `tdmrep.json` 1 MiB) und innerhalb einer Frist für die ganze Anfrage (60 s, PDFs 180 s). Vorher konnte eine Site
  eine endlose oder tröpfelnde Antwort schicken, die den Speicher füllte oder den Crawl, und damit Stoppen oder das
  Schließen des Assistenten, für immer festhielt: Der HTTP-Timeout begrenzt nur jeden einzelnen Lesevorgang am
  Socket. Eine größere Seite wird übersprungen, eine langsamere scheitert, beide mit Grund im Crawl-Protokoll;
  Stoppen wartet nicht mehr auf eine Seite, die noch ankommt.
- Community-Packs werden nur über einen Quarantäne-Ordner und nach ihren Prüfungen installiert: eine ID ohne
  Pfade, genau die aufgeführten Dateien mit ihren Hashes, keine Links, Sonder- oder Binärdateien, jedes Image, das
  ein Dockerfile bezieht (`FROM`, `COPY --from`, `RUN --mount=...,from=`, auch in einem kleingeschriebenen
  `dockerfile`), per Digest gepinnt, außer es ist ein offizielles Basis-Image, die Sperrliste für Shell-Code auf
  jeder Skriptzeile, die nicht aus der mitgelieferten Vorlage stammt, und `bash -n`. Eine Kopie des
  mitgelieferten Rockchip-Targets besteht diese Prüfungen. Ein Pack erscheint erst unter seinem endgültigen Namen,
  wenn es vollständig ist, und nie über einem bestehenden Target (auch nicht, wenn sich nur die Groß- und
  Kleinschreibung unterscheidet). Vorher konnte ein Link in einem Community-Modul jede Datei des Benutzers nach
  `targets/` kopieren.
- Der Docker-Build-Kontext folgt nie symbolischen Links oder Sonderdateien eines Targets, ein verlinktes Dockerfile
  wird abgelehnt, und der Target-Name eines Builds kann keinen Pfad enthalten.
- `OPENAI_CUSTOM_HEADERS` und `ANTHROPIC_CUSTOM_HEADERS` werden beim Start der CLI, der GUI und des MCP-Servers
  mit einer Warnung verworfen, außer die Einstellung `ai_allow_sdk_env_headers` ist wahr (ein `"false"` in
  Anführungszeichen gilt als falsch; Einstellungen, die sich nicht lesen lassen, gelten als falsch): Sie könnten
  den gespeicherten API-Schlüssel ersetzen und jeden Server erreichen, auch lokale. Die Adapter für OpenAI und
  OpenAI-kompatible Server schicken den gespeicherten Schlüssel außerdem als ausdrücklichen
  `Authorization`-Header.
- Die Debug-Ausgabe der SDKs der Anbieter und der HTTP-Bibliotheken (Optionen der Anfragen, Prompts, Header)
  bleibt aus, außer `--debug-http` ist angegeben: Ihre Logger gehen nie unter INFO, auch nicht mit
  `--log-level DEBUG`, und `ANTHROPIC_LOG=debug` / `OPENAI_LOG=debug` werden auf `info` gesenkt.
- Admin-API-Schlüssel von Claude (`sk-ant-admin01-`) werden abgelehnt, bevor eine Anfrage gesendet wird, auch
  wenn sie mit Leerzeichen davor oder danach gespeichert wurden.
- Der Tresor für Zugangsdaten wird unter einer Sperre über Prozesse hinweg geändert: GUI, CLI und ein MCP-Server,
  die gleichzeitig schreiben, verlieren nicht mehr die Änderungen der anderen.
- Ein Schlüsselbund, der nichts behält (etwa `keyring.backends.null`) oder nicht innerhalb von 3 Sekunden
  antwortet, wird nicht mehr verwendet; bisher waren damit gespeicherte Geheimnisse nach dem nächsten Start
  unlesbar. Ein Schlüsselbund-Backend, das ohne Verschlüsselung speichert (`keyrings.alt`), wird ebenfalls nicht
  verwendet: Das wäre `secret_storage: insecure`, und das kannst nur du wählen.
- Unter Windows bleibt der Schlüssel des Tresors auf dem lokalen Rechner, statt mit dem Benutzerprofil zu wandern.
- Der Secrets-Speicher lehnt einen Ort innerhalb einer Git-Arbeitskopie ab (setze `LLM_FRAMEWORK_HOME` woanders
  hin).
- Namen von Geheimnissen mit Zeilenumbrüchen oder Steuerzeichen werden abgelehnt, und das Audit-Log der Secrets
  maskiert sie, sodass ein Name (oder ein Benutzername) keine Audit-Einträge fälschen kann.
- **Zugangsdaten werden in jedem Log geschwärzt**: in den eigenen Logs des Frameworks, in denen der SDKs der
  Anbieter und der HTTP-Bibliotheken (anthropic, openai, httpx, httpcore, urllib3, botocore, google.auth, azure,
  msal), in Tracebacks und in den Zusatzfeldern des JSON-Logformats. Geschwärzt werden die Werte von Headern mit
  Zugangsdaten (`Authorization`, `x-api-key`, `api-key`, `x-goog-api-key`, `Cookie`, `Set-Cookie`, auch in JSON
  mit maskierten Anführungszeichen), Anmelde- und Schlüsselparameter in URLs und in formularkodierten
  Token-Anfragen (`code`, `state`, `key`, `access_token`, `token`, …) und jedes bekannte Schlüsselformat.
- Die Schwärzung findet Zugangsdaten auch an ihrem Kontext, gleich in welchem Format: ein Bearer-Token, das eine
  Fehlermeldung eines Servers zitiert, Werte in Token-Form in Feldern wie `api_key`, `secret`, `token` oder
  `password` und Umgebungszeilen wie `HF_TOKEN=...` oder `export OPENAI_API_KEY=...`. Tokenizer-Einstellungen in
  Build-Logs (zum Beispiel `"pad_token": "<|...|>"`) bleiben lesbar.
- Die Schwärzung lässt sich nicht mehr durch präparierten Text ausbremsen: Eine Fehlermeldung oder ein Build-Log
  mit einer langen Folge von `sk-` brauchte vorher Minuten.
- **Eine Tabelle der Secret-Formate** (`orchestrator/security/secret_patterns.py`) für den Secret-Scan und die
  Schwärzung zur Laufzeit, die vorher nur fünf Formate kannte. Neu: Schlüssel von Unsloth Studio (`sk-unsloth-`),
  kurzlebige Bedrock-Schlüssel (`bedrock-api-key-`), LocalAI-Schlüssel (`lai-`), geheime age-Schlüssel
  (`AGE-SECRET-KEY-1...`), Organisations-Tokens von Hugging Face (`api_org_`), Test-Schlüssel von Stripe
  (`sk_test_`, `rk_test_`), Zeilen mit `Authorization: Bearer` und Werte in Anführungszeichen, die `api_key`,
  `secret` oder `token` zugewiesen werden (ein Name in Kleinbuchstaben wie `"community_signing_key"` wird nicht
  gemeldet). Die Schwärzung zur Laufzeit erfasst jetzt auch JWTs, Blöcke privater Schlüssel, Passwörter in URLs,
  routingfähige GitLab-Tokens vollständig, Tokens von xAI und Replicate, die `_` oder `-` enthalten, und
  Schlüssel direkt hinter einem Buchstaben einer anderen Schrift (`令牌hf_...`, `éAKIA...`). Der Secret-Scan meldet
  einen Wert im exakten Format eines Tokens, das der Push-Schutz von GitHub blockiert (etwa `hf_` gefolgt von 34
  Buchstaben oder `ghp_` gefolgt von 36 Buchstaben und Ziffern), auch wenn er wie ein Platzhalter aussieht oder
  seine Zeile `secret-scan: allow` trägt, und auch in Textdateien über 5 MB, die die übrigen Regeln überspringen:
  GitHub lehnt einen solchen Push ab, so unecht der Wert auch aussieht, und kennt die Markierung nicht. Tests bauen
  solche Werte zur Laufzeit zusammen. Nur `--history` überspringt die zwei dokumentierten unechten Testwerte, die
  alte Commits noch enthalten; `.secret-scan-allow` listet sie über den sha256 des exakten Werts, nie über den Wert
  selbst. Jeder andere Modus meldet sie weiterhin. Ein gelisteter Testwert verdeckt nichts anderes: Ein längerer
  Wert eines anderen Formats, der ihn enthält, wird weiterhin gemeldet, die übrigen Regeln lesen die Zeile auch ohne
  ihn, und eine Zeile der Liste mit einem Steuer- oder unsichtbaren Zeichen ist fehlerhaft (Exit-Code 2), damit ein
  Kommentar keinen Eintrag verstecken kann.
- API-Schlüssel, die Dittos Anbieterschicht im Speicher hält, erscheinen in `str()` und `repr()` als
  `<redacted>` und lassen sich nicht mit pickle serialisieren.
- Ein Schlüssel eines eigenen OpenAI-kompatiblen Servers gehört zu der Adresse, für die er gespeichert wurde
  (Schema, Host und Port): Nach einer Änderung der Basis-URL wird er nicht mehr mitgeschickt. Ein Schlüssel, den
  eine frühere Version gespeichert hat, wird an die Adresse gebunden, mit der er als Nächstes genutzt wird.
  Claude-Admin-API-Schlüssel werden auch für eine Claude-API-Vorlage in deinem eigenen Anbieterkatalog
  abgelehnt, die sie nicht nennt.
- Härtung der Anmeldung: Der Hinweis zum Schlüssel in den KI-Einstellungen bindet einen Schlüssel einer früheren
  Version nie an eine Adresse, die gerade noch eingetippt wird; Abmelden entfernt auch einen solchen Schlüssel;
  die Prüfung der Zugangsdaten schickt einen an eine Adresse gebundenen Schlüssel nur an diese Adresse; eine
  Schlüsselvariable einer lokalen Engine in deinem eigenen Katalog geht nur an die Adresse, die der Katalog
  nennt. Ein nicht geheimes Feld (Workspace-ID, Projekt) lässt sich nicht mehr mit einem Header für
  Zugangsdaten wie `Authorization` oder `x-api-key` angeben, wo es den gespeicherten Schlüssel ersetzen würde.
  Eine gescheiterte Token-Erneuerung nennt nur die Art des Fehlers, und eine Meldung des Anbieters, die das
  Refresh-Token zitiert, wird ohne es angezeigt. Ein Schlüssel einer früheren Version wird an die Adresse
  gebunden, bevor eine Prüfung mit eingetippten Feldern ihn dorthin schickt.
- Die Anmeldung nimmt nie ein Geheimnis von der Kommandozeile (`ditto login` hat keine Option für einen
  Schlüssel, und ein unbekannter Anbietername oder `--method`-Wert, der wie ein Schlüssel aussieht, wird nicht
  ausgegeben), zeigt einen Schlüssel nur mit seinen letzten vier Zeichen (auch in `auth status --json`, und als
  `REDACTED`, wenn ein Fehlertext oder Kontoname des Servers ihn wiederholt, auch in Fehlern von Dittos Chat),
  fragt nach, bevor ein Schlüssel über reines `http://` an einen anderen Rechner geht (auch im Anmeldebereich und
  im Schlüsselfeld der KI-Einstellungen; `--yes` in Skripten), und wählt nie selbst, wie der Tresor geschützt
  wird: `--insecure-storage` ersetzt nie einen Schutz, der funktioniert, und auch keinen Credential-Helper und
  keine Passphrase, die du gewählt hast. Ein Schlüssel mit einem Leerzeichen, einem Zeilenumbruch oder einem
  unsichtbaren Zeichen darin wird abgelehnt, bevor etwas gesendet oder gespeichert wird.
- Das Builder-Log zeigt jede Meldung als reinen Text, sodass Namen aus Community-Rezepten, Dittos Antworten oder
  Build-Ausgaben nicht als HTML erscheinen (Links, Bilder, Formatierung).
- Hardware-Profile enthalten keine identifizierenden Werte: Die Probe-Skripte schreiben weder den Hostnamen noch die
  RAM-Teilenummern oder die CPU-ID, die Serial-Zeile von `/proc/cpuinfo` und das Serial-Attribut von USB-Geräten
  werden nie gelesen, und freier Text wird auf druckbares ASCII reduziert. Werte, die auf einem Board gelesen werden,
  werden vor dem Schreiben geprüft, und der SSH-Scan behandelt die Antwort des Boards als nicht vertrauenswürdig
  (Größengrenze, strenger Parser).
- **Aufrufe, die etwas ändern, laufen nur, wenn sie festgehalten werden** (Ditto und MCP-Clients): Bevor ein solcher
  Aufruf läuft, wird ein Eintrag `started` ins Audit-Log geschrieben; lässt er sich nicht schreiben, wird der Aufruf
  abgelehnt, und eine Frage dazu wird nicht gestellt. Lesende Aufrufe bleiben verfügbar. Bisher wurde ein
  gescheiterter Audit-Eintrag nur protokolliert und der Aufruf lief trotzdem, und der einzige Eintrag entstand nach
  dem Handler, sodass ein Absturz mitten in einer Änderung keine Spur hinterließ.
- **Jedes Tool-Ergebnis wird zentral gefiltert** (Ditto und MCP-Clients): Secrets werden in jeder Zeichenkette eines
  Ergebnisses geschwärzt, externer Text (Build-Logs und Stufennamen, Healing-Vorschläge, Build-Manifeste, Auszüge
  aus der Wissensbasis, Doctor-Meldungen, Bezeichnungen und Hinweise von Targets aus der `target.yml`) wird von
  unsichtbaren und bidirektionalen Steuerzeichen befreit und gekürzt, andere Zeichenketten werden auf 16 KiB
  gekürzt, und ein Ergebnis über 256 KiB wird mit einem Hinweis abgelehnt, wie man weniger anfordert. Der Fehler
  eines gescheiterten Aufrufs wird vor dem Schwärzen der Secrets genauso bereinigt, und ein MCP-Client bekommt den
  Teil, der externen Text zitiert, als nicht vertrauenswürdige Daten eingepackt, wie Dittos Modell. Bisher wurden
  nur Fehler, Warnungen und Log-Zeilen geschwärzt, nur Auszüge aus der Wissensbasis bereinigt, Log-Zeilen und
  Ergebnisse hatten keine Größengrenze, und ein MCP-Client bekam Fehlertext eines Tools unmarkiert.
- **Neue Tools erben nie eine gelockerte Einstellung der Stufe, und die Obergrenze eines Tools gilt**: Die
  Berechtigungsdatei hält fest, welche Tools du gesehen hast, und ein später hinzugekommenes Tool nutzt seine eigene
  Vorgabe (oder die empfohlene Einstellung der Stufe), bis du es gesehen hast; eine einmal erlaubte Stufe erlaubt also
  nicht mehr jedes Tool, das ihr später hinzugefügt wird. Die eigene Vorgabe eines Tools (zum Beispiel `deny` für ein
  optionales Tool) gilt auch, wenn seine Stufe erlaubt ist, öffnet aber nie eine Stufe, die du verweigert hast, und
  ein Tool, das andere Geräte erreicht oder Daten löscht, fragt höchstens. Bisher konnte eine Einstellung für ein
  einzelnes Tool ein solches Tool ohne Rückfrage erlauben. Eine Berechtigungsdatei aus 3.0.0 zählt ihre elf Tools als
  gesehen; 3.0.0 selbst liest eine von dieser Version gespeicherte Datei als beschädigt und verweigert jedes Tool.
- MCP und Ditto: `start_build` nimmt für `model` und `dataset_path` keinen Pfad mehr an (ein MCP-Client konnte jede
  lesbare Datei, etwa `~/.ssh/id_ed25519`, in einen Build einbinden und über das Log zurücklesen). Nur Verweise in
  den Ordnern, die die KI nutzen darf, werden angenommen; die aufgelöste Datei muss in ihrem Ordner und außerhalb
  jedes gesperrten Ordners liegen (Schlüssel, Anmeldedaten, die eigenen Anmeldungen der KI-Clients, das
  Hugging-Face-Token, der persönliche Ordner des Frameworks, Systemordner), entschieden über die Identität der
  Datei, sodass ein Dateisystem ohne Unterscheidung von Groß- und Kleinschreibung keinen verstecken kann. Dateien
  mit Hardlinks werden abgelehnt, ein Ordner wird nur echt unterhalb seines Label-Ordners und ohne gesperrten
  Ordner, Datei mit Hardlink, Socket, Gerät oder Pipe darin genutzt, und die Datei wird unmittelbar vor jedem Start
  eines Build-Containers erneut geprüft. Eine Hugging-Face-Repo-ID eines KI-Aufrufers wird nicht mehr als lokaler
  Pfad nachgeschlagen, weder vom Build-Service noch von der Build-Engine (die einen gleichnamigen Ordner unter ihrem
  Arbeitsverzeichnis einband). Ablehnungen nennen das Label, nie den echten Pfad.
- **MCP über HTTP ist für Webseiten und DNS-Rebinding geschlossen.** In 3.0.0 kam eine Seite von einem beliebigen
  Port auf localhost durch die Origin-Prüfung (nur das Token hielt sie auf), die Host-Prüfung nahm einen
  Loopback-Namen mit jedem Port an, und alle HTTP-Clients teilten sich ein Token und einen Audit-Akteur. Jetzt wird
  jede Anfrage mit `Origin`-Header (auch ein CORS-Preflight) abgelehnt, bevor das Token geprüft wird, der
  `Host`-Header muss ein Loopback-Name mit dem eigenen Port des Servers sein (im Framework und noch einmal im SDK),
  der Server hält höchstens 8 Sitzungen (vorher 10.000) und nimmt höchstens 1 MiB pro Anfrage an (vorher 4 MiB),
  eine Sitzung und eine offene Fragerunde des Protokolls 2026-07-28 antworten nur dem Token, das sie geöffnet hat,
  und kein Token wird angenommen (und der Server startet nicht), solange andere Benutzer die Token-Datei lesen oder
  ändern können oder sie einem anderen Benutzer gehört.
- **Kein vorgebautes llama.cpp in Targets**: Erzeugte Module, Community-Exporte und Target-Pakete in Quarantäne
  werden abgelehnt, wenn ein Dockerfile ein vorgebautes llama.cpp-Image bezieht (nach seinem Namen, auch per Digest
  festgelegt, oder eines der früheren Laufzeit-Images des Frameworks) oder llama.cpp-Programme aus einem anderen Image
  kopiert (auch über eine Build-Stufe, die es nur nennt), oder wenn ein Dockerfile, ein Skript (auch eines ohne
  bekannte Endung, das mit `#!` beginnt) oder eine YAML-Datei llama.cpp-Release-Archive oder -Programme herunterlädt
  (auch über eine Pipeline wie `curl ... | tar -x`), eine Paketquelle mit vorgebauten `llama-cpp-python`-Wheels nutzt oder ein llama.cpp-Paket installiert:
  Das Framework teilt Rezepte, keine Binärdateien. `nvidia/cuda`-Images gelten nicht mehr als offizielle Basis-Images
  und brauchen einen Digest, und ein llama.cpp-Image wird als Basis-Image eines Moduls abgelehnt.
- Der llama.cpp-Server auf einem Board lauscht nur auf 127.0.0.1 und braucht einen Schlüssel, der auf dem Board
  erzeugt wird und es nie verlässt. Ein Deployment erreicht nur das Board, mit dessen Host-Schlüssel das Profil
  erstellt wurde, läuft als unprivilegierter Benutzer (root wird abgelehnt) und nutzt sudo nur für eine systemd-Unit,
  die du in den Deploy-Einstellungen erlaubt hast, nie mit einer Passwortabfrage. Jeder Wert, der vom Board gelesen
  wird (Home-Ordner, Benutzer-ID, Architektur), wird geprüft, bevor er einen Befehl erreicht.
- **Nur eine Person ändert, was eine KI darf.** Die Tool-Berechtigungen (`tool_policy.json`) und der MCP-Token-Speicher
  (`mcp_tokens.json`) sind mit einem Schlüssel signiert, der aus dem Audit-Schlüssel im Schlüsselbund des Systems
  abgeleitet ist. Eine von Hand bearbeitete, beschädigte, mit einem anderen Schlüssel signierte oder unsignierte Datei
  verbietet jedes Tool, und der Token-Speicher nimmt kein Token an, bis eine Person sie neu einrichtet; `llm-cli doctor`
  meldet das. Bisher konnte jeder, der in den Benutzerordner schreiben konnte, jedes Tool erlauben oder ein Token
  hinzufügen, ohne dass es auffiel. Jede Änderung der Berechtigungen wird mit den alten und neuen Entscheidungen
  (Akteur `cli` oder `gui`) im Audit-Log festgehalten, bevor sie wirkt, und abgelehnt, wenn sich der Eintrag nicht
  schreiben lässt. Speichern bekommt eine Revisionsnummer unter einer Sperre, die GUI, CLI und MCP-Server teilen; ein
  Speichern auf Grundlage einer älteren Revision wird abgelehnt, damit ein früher geöffnetes Fenster eine
  zwischenzeitliche Änderung nicht mehr rückgängig machen kann. Die neueste gespeicherte Revision jeder Datei steht
  zusätzlich im Schlüsselbund, daher verbieten auch eine ältere signierte Kopie, die darüber zurückgelegt wird (eine
  widerrufene dauerhafte Freigabe oder ein widerrufenes Token käme zurück), und eine entfernte Berechtigungsdatei
  alles. Ohne Schlüsselbund hat die Signatur keinen Schlüssel, wie die Audit-Kette; eine solche Datei wird abgelehnt,
  solange das Audit-Log mit Schlüssel geführt wird.
- **Ausstehende Freigaben und Bitten, nicht mehr zu fragen, sind Rückfragen wie alle anderen.** Sie zählen zu den
  30 Rückfragen pro Stunde; ein Nein im Freigabefenster verhindert für 10 Minuten, dass dir derselbe Aufruf erneut
  vorgelegt wird, und zählt zur Pause des Tools, wie ein Nein im Dialog des Clients; ein kürzliches Nein, ein
  pausiertes Tool oder eine aufgebrauchte Stunde lehnen eine neue ausstehende Freigabe ab; und die Grenze von 5
  offenen ausstehenden Freigaben ist eine protokollierte Ablehnung, die nie als gescheiterter Aufruf zählt. Vorher
  konnte ein Client, der nicht fragen kann, Rückfragen ohne diese Grenzen einreihen.

- **Kein implizites Hugging-Face-Token**: Builds übergaben huggingface_hub `token=None`, wenn kein Token gespeichert
  war, sodass ein Token aus `HF_TOKEN` oder dem Hugging-Face-Home-Ordner verwendet wurde, ohne dass die Person es dem
  Framework gegeben hatte (G37). Jeder Hugging-Face-Aufruf eines Builds sendet jetzt das gespeicherte Token oder
  `token=False`.
- **Image-Builds lesen nie den MCP-Protokollstrom**: `docker buildx build` lief mit der Standardeingabe des Servers,
  die über stdio der Protokollstrom ist (G25); es bekommt jetzt `stdin=DEVNULL`.
- **Deployment-Pakete nehmen Images nur per Digest festgelegt oder hier gebaut**: Das voreingestellte
  Inferenz-Image war `...:latest`, und ein fehlgeschlagenes Ziehen über ein Tag wurde verschluckt, sodass ein Paket
  enthalten konnte, was die Registry gerade auslieferte (G17). Ein Image kommt jetzt nur als `name@sha256:...` ins
  Paket oder wenn es auf diesem Rechner gebaut wurde; ein Tag wird mit Grund abgelehnt, ein fehlendes festgelegtes
  Image nur über seinen Digest gezogen, und ein fehlgeschlagener Export bricht das Paket ab.
- **Deployments leiten nie den MCP-Protokollstrom weiter**: Ohne paramiko liefen `ssh` und `scp` mit der
  Standardeingabe des Frameworks, die über MCP-stdio der Protokollstrom ist (G25); sie bekommen jetzt
  `stdin=DEVNULL`.

## [3.0.0] - 2026-10-10
**Release für Reparatur, Sicherheit und MCP.** Die Build-Pipeline funktioniert wieder von Anfang bis Ende (mit
echten Konvertierungen geprüft), jede bekannte Schwachstelle in den Abhängigkeiten ist beseitigt, KI-Clients
können das Framework über das Model Context Protocol steuern, und Konvertierungs-Container laufen in einer
Sandbox.

### Hinzugefügt
- **MCP-Server** (`llm-cli mcp serve`, offizielles SDK `mcp` 2.3.0, Spezifikation 2026-07-28): Claude Code,
  Claude Desktop oder VS Code können Targets auflisten, Capabilities prüfen, Builds starten und abbrechen, Logs und
  Artefakte lesen, die Wissensbasis durchsuchen, den Doctor ausführen und über Self-Healing-Vorschläge
  entscheiden. Standardmäßig über stdio; optional Streamable HTTP auf Loopback mit einem Bearer-Token je
  Benutzer. `llm-cli mcp config` gibt die Client-Einrichtung aus, `--write claude-desktop` trägt sie in Claude
  Desktop ein.
- **Tool-Registry mit Berechtigungen und Audit-Log**, gemeinsam genutzt von Ditto und dem MCP-Server: typisierte
  Tools mit Risikostufen (Lesen, lokales Schreiben, extern, verboten), Berechtigungen, die einmal bei Dittos erster
  Einrichtung abgefragt werden (GUI-Dialog, `llm-cli ditto setup`, `llm-cli tools permissions`), Bestätigungen
  über MCP-Elicitation und ein HMAC-verkettetes Audit-Log (`llm-cli tools audit --verify`).
- **Target-Capabilities** (Block `capabilities:` in `target.yml`, pydantic-Schema): Boards, Formate,
  Quantisierungen je Board, Standardwerte und Kalibrierungsbedarf. GUI, CLI, Build-Dienst und MCP-Tools bieten
  genau das an, was ein Target angibt, und prüfen genau das.
- **Build-Dienst**: ein typisierter Einstiegspunkt für Builds (prüfen, starten, Status, abbrechen, Logs,
  Artefakte).
- **`llm-cli doctor`**: deterministische Gesundheitsprüfungen (Docker, Speicherplatz, Targets, Sicherheit,
  Tool-Berechtigungen, Kalibrierungsdaten) mit Hinweisen zur Behebung, gestuften automatischen Korrekturen und
  JSON-Ausgabe (angelehnt an „Mojo“ in rag4ll).
- **Kuratierter Kalibrierungstext** (`data/calibration`, MIT, mit Prüfsummen gesicherte Textsätze auf Deutsch,
  Englisch und zur Tool-Nutzung) für Importance Matrices.
- **Build-Manifeste**: Jeder Build ergänzt `build_info.json` um seine Modelldateien und deren SHA256-Hashes; das
  Deployment-Fenster listet Builds anhand dieser Manifeste.
- **Echter Build-Abbruch und Zeitlimits je Stufe**.
- **SSH-Vertrauen bei der ersten Verbindung** (Trust on First Use): Fingerabdrücke von Host-Schlüsseln bestätigt
  der Benutzer, gespeichert werden sie in der eigenen `known_hosts` des Frameworks; ein Ed25519-Schlüssel des
  Frameworks wird einmalig auf dem Gerät installiert („Pairing“).
- **Echte CI-Workflows** (Lint, Tests unter Ubuntu mit 3.11 bis 3.14 und unter Windows, bandit, pip-audit,
  Docker-Build und Smoke-Tests einschließlich der Sandbox) und Dependabot.
- Unterstützung für Python 3.14; Zustandsordner je Benutzer `~/.llm-framework` (`LLM_FRAMEWORK_HOME`).

### Geändert
- **Rockchip-Pipeline neu aufgebaut** und von Anfang bis Ende geprüft: llama.cpp v0.6.0 (GGUF einschließlich
  IQ-Typen und Importance Matrix), RKLLM-Toolkit 1.3.1 und RKNN-Toolkit2 2.3.2, jedes in einer eigenen venv;
  Modelle werden auf dem Host bereitgestellt (Hugging-Face-Repo oder lokaler Pfad), Verteilung nach Format
  `gguf`/`rkllm`/`rknn`, Standardwerte passend zum Board.
- Image-Builds nutzen BuildKit, wenn es verfügbar ist, und melden die echte Ursache von Fehlern; die Images
  einzelner Builds werden nach dem Build entfernt.
- Der Kalibrierungsdatensatz ist von der Importance Matrix getrennt (IMatrix nur für GGUF, der Datensatz geht
  direkt an RKLLM/RKNN).
- Das gespeicherte Hugging-Face-Token erreicht die Builds (eine gemeinsame Build-Engine).
- Relative Datenpfade werden gegen das Installationsverzeichnis aufgelöst, nicht gegen das Arbeitsverzeichnis.
- Self-Healing: Vorschläge warten auf eine ausdrückliche Entscheidung (GUI-Dialog, CLI-Abfrage oder MCP-Tool);
  angewendet werden nur feste, vom Framework definierte Korrekturen (weniger Compile-Jobs, nachdem einem
  Image-Build der Arbeitsspeicher ausging, Aufräumen von Framework-Images); KI-Vorschläge sind nur Ratschläge.
- Ditto: Fehler und Ablehnungen lösen Ausnahmen aus, statt als Antworten angezeigt zu werden; API-Schlüssel
  werden je Aufruf übergeben; Text aus der Wissensbasis, Logs und Probe-Dateien kommen als bereinigte, als nicht
  vertrauenswürdig markierte Blöcke in die Benutzernachricht.
- Abhängigkeiten mit Hash-Pins aktualisiert (Poetry-2-Lock, `requirements*.txt`); `pyside6-essentials` statt des
  vollständigen PySide6; `pydantic` und `mcp` hinzugefügt.
- Installer und Smoke-Test verlangen Python 3.11 bis 3.14.
- Eine einzige Versionsquelle (`orchestrator.__version__`).
- **Die Konfiguration wird nie mehr aus dem Arbeitsverzeichnis gelesen**: `LLM_FRAMEWORK_CONFIG_DIR`, dann
  `configs/` der Installation, dann `~/.llm-framework` (`framework.yml` im aktuellen Verzeichnis wird ignoriert).
- Der Secrets-Speicher liegt im Ordner des Benutzers (`~/.llm-framework/secrets.store`, einmalig vom alten Ort
  herüberkopiert); der Schlüssel des Audit-Logs hat einen eigenen Keyring-Eintrag.
- Capabilities: `default_board` (das Board, das die Build-Skripte verwenden, wenn keins angegeben ist), Aliasse
  für Quantisierungen je Format (z. B. `INT8` -> `Q8_0`), Board-Namen ohne Beachtung der Groß- und
  Kleinschreibung; CLI, Build-Dienst und GUI übergeben genau die Werte, die sie geprüft haben. IMatrix ohne eigenen
  Datensatz ist wieder gültig (mitgelieferter Kalibrierungstext).
- `build start -o` wird gegen das Arbeitsverzeichnis aufgelöst, der konfigurierte Standard gegen die
  Installation; `deploy list-artifacts` und `deploy package` lesen die Build-Manifeste im konfigurierten
  Ausgabeordner.

### Entfernt
- Die Abhängigkeiten LangChain, python-dotenv, pywin32 und winshell.
- Das gespeicherte SSH-Passwort (`target_password`) und sein Menüeintrag: Geräte nutzen den Schlüssel des
  Frameworks.
- Dittos generierte Füll-Kalibrierung (sie lud eine URL, die 404 liefert, und fiel auf wiederholte Sätze
  zurück).

### Behoben
- Viele Abstürze und tote Pfade: Enum für den Build-Status, GUI-Builds, die stoppten, sobald ein Slot
  zurückkehrte, Healing-Dialog, Chatfenster (schickte jede Frage zweimal, stürzte mit PySide6 6.12 bei jeder
  Nachricht ab), Import des Modul-Assistenten, harte Abbrüche und Log-Stufen der CLI, Deployment von Ordnern und
  Archiven.
- RKNN: Datensatz-Mounts ersetzen nicht mehr den Modell-Mount; JSON-Datensätze behalten ihre Endung; veraltete
  Artefakte anderer Boards werden nie ausgeliefert.
- Neustartschleife des Entrypoints, wenn der Host-Benutzer root ist; Prüfsummen mit Windows-Zeilenenden.
- Der Abbruch eines Builds kann nicht mehr von einer späteren Stufe rückgängig gemacht werden (Sicherheitsscan,
  Image-Build und Stufen-Container werden gestoppt); nur echte Lese-Zeitüberschreitungen zählen als
  Zeitüberschreitung; ein erneuter Versuch nach einem Healing-Fix behält seine eigene Build-ID und sein Log; eine
  beschädigte Audit-Datenbank oder ein beschädigter Benutzerordner hindert das Framework nicht mehr am Start.
- Audit-Log: entfernte neueste Einträge bleiben auch nach späteren Aufrufen erkennbar; keine falschen
  Manipulationsalarme nach einem fehlgeschlagenen Commit, einem fehlgeschlagenen Schreiben der Ankerdatei oder
  einem Eintrag mit einem anderen Schlüssel; das Audit-Log wird weitergeführt, nachdem die Datenbank beiseitegelegt
  wurde; `llm-cli tools audit --archive` beginnt eine neue Kette und lässt die alte prüfbar.
- Secrets: der Speicher einer früheren Version wird genau einmal übernommen (auch in einen bestehenden Speicher im
  Benutzerordner), und ein beschädigter wird gemeldet; ein laufender MCP-Server sieht Secrets, die ein anderer
  Prozess hinzugefügt oder gelöscht hat; nichts wird mehr in das Arbeitsverzeichnis geschrieben (Audit-Log der
  Secrets, Modellordner).
- Tool-Berechtigungen: eine tief verschachtelte Berechtigungsdatei oder ein Link, dessen Ziel fehlt, verweigert
  jedes Tool, wie andere Schäden auch.
- MCP: ein ausdrückliches „Nein“ bleibt endgültig, auch wenn sich die Berechtigungen inzwischen ändern; jede
  Ablehnung wird im Audit-Log festgehalten; Fehler beim Schreiben der Claude-Desktop-Konfiguration werden ohne
  Traceback gemeldet; `XDG_CONFIG_HOME` wird unter Linux beachtet; `-v` schreibt keine Log-Zeilen mehr in den
  Protokollkanal.
- Builds: ein abgebrochener Build hinterlässt kein Manifest und kein Archiv; ein fertiger Build gibt seinen Slot
  frei, bevor sein Endzustand sichtbar wird; Links im Ausgabeordner werden nie verfolgt; die GUI sendet für ein
  Target ohne Capabilities kein Board, außer es wurde eines eingetippt.

### Sicherheit
- **Konvertierungs-Container in der Sandbox**: Host-Benutzer statt root, kein Netzwerk, keine Capabilities,
  no-new-privileges, schreibgeschütztes Image-Dateisystem, Grenzen für Prozesse und Arbeitsspeicher.
- Crawler: SSRF-Schutz mit Verbindungs-Pinning, das gegen DNS-Rebinding sicher ist, robots.txt nach RFC 9309,
  Grenzen für Weiterleitungen und Anfragen.
- Modulerzeugung: strenge Prüfung von Namen, Images, Paketen, Flags und Shell-Code (Sperrliste, `bash -n`),
  Beschränkung auf `targets/`, kein Überschreiben bestehender Targets.
- Chat-Ausgaben werden maskiert; Links öffnen sich erst nach Bestätigung.
- Das Entpacken von Archiven blockiert Path Traversal, Links und Gerätedateien; Secrets werden in Logs
  geschwärzt.
- pip-audit meldet keine bekannten Schwachstellen (vorher 279); GitHub Actions sind per Commit-SHA gepinnt.
- Ein Projektordner kann die Einstellungen eines darin gestarteten MCP-Servers nicht mehr ändern (z. B. die
  Quell-Repositories der Build-Toolchain).
- Ein beschädigter Secrets-Speicher wird nie überschrieben; Secrets werden atomar mit den Rechten 0600
  geschrieben.
- Audit-Log: eine Schreibtransaktion je Eintrag (keine verzweigten Ketten bei mehreren Prozessen), eine
  Schlüssel-ID je Log, eine Ankerdatei, die entfernte neueste Einträge aufdeckt.
- Eine beschädigte Datei mit Tool-Berechtigungen verweigert jedes Tool, statt auf lockerere Standardwerte
  zurückzufallen.
- MCP: Bestätigungen zeigen jedes Argument vollständig (zu lang: abgelehnt), nur eine echte Antwort des Benutzers
  zählt als Zustimmung, abgelehnte und abgebrochene Aufrufe werden im Audit-Log festgehalten, Client-Snippets
  sind korrekt gequotet, und die Sicherungskopie der Claude-Desktop-Konfiguration ist privat.
- Der Startbefehl des MCP-Servers, der in Client-Konfigurationen geschrieben wird, importiert nie Code aus dem
  Ordner, in dem der Client ihn startet (`python -P`); Snippets für Windows sind für cmd und PowerShell gequotet;
  unsichtbare und kombinierende Zeichen in Bestätigungstexten werden maskiert angezeigt; Fehlermeldungen von Tools
  werden geschwärzt, bevor sie einen Client erreichen.
- **Secret-Scan** (`scripts/secret_scan.py`, nur Standardbibliothek): API-Schlüssel von Anbietern, Tokens,
  private Schlüssel, Zertifikate und Dateien mit Zugangsdaten werden von einem pre-commit- und einem
  pre-push-Hook (`make hooks`) und von einem CI-Job über die gesamte Historie abgewiesen. Persönliche
  Zeichenketten (E-Mail-Adresse, Benutzername) kommen in die nie committete Datei `.git/info/secret-patterns`;
  Funde werden geschwärzt ausgegeben.

## [2.4.0] - 2026-01-14
**Rekonstruierter Eintrag.** Die Versionen 2.0 bis 2.4 entstanden ohne Changelog-Einträge; diese Zusammenfassung
stammt aus den Kopfzeilen der Quelldateien und der Git-Historie (2025-12-13 bis 2026-01-14).

### Hinzugefügt
- Self-Healing-Manager (Regex-Heuristiken plus KI-Analyse von Build-Logs).
- Offline-Modus für Ditto mit kleinen lokalen Modellen, Telemetrie (Opt-in), Updater, Community-Hub.
- IMatrix „Smart Calibration“ für die GGUF-Quantisierung.
- Eine einzige Quelle der Wahrheit für Projektquellen (`configs/project_sources.yml`, `SSOT_ARCHITECTURE.md`,
  `validate_ssot.py`, `sync_ssot.py`).
- Deutsche Stimmmodelle (GLaDOS, Thorsten) im Quellenkatalog; Transparenz-Manager (experimentell).

### Geändert
- Container-Images zentral konfigurierbar; Sicherheitshärtung der GUI-Fenster.

## [1.7.0] - 2025-12-10
**Hardware- und Deployment-Update.** Schließt die Lücke zwischen Build und Betrieb mit einem Deployment-Manager
ohne zusätzliche Abhängigkeiten und voller Unterstützung für Intel-Hardware.

### Hinzugefügt
- **Deployment-Manager:** Integrierter SSH/SCP-Client, um Artefakte direkt auf Edge-Geräte zu übertragen. Mit
  einem Modus „Zero-Dependency“ (nutzt Programme des Systems oder Paramiko) und einer Ping-Schleife „Network
  Guard“ für eine stabile Verbindung.
- **Intel-Ökosystem:** Volle Unterstützung für Intel IPEX-LLM und OpenVINO. `hardware_probe` erkennt jetzt
  Intel-Arc-GPUs, iGPUs und NPU-Beschleuniger (Meteor Lake).
- **Live-Überwachung:** Anzeige der CPU- und RAM-Auslastung des Build-Containers in Echtzeit direkt in der GUI
  (ersetzt externe Werkzeuge wie `ctop`).
- **Visuelle Identität:** „Ditto“ erwacht zum Leben! Dynamische Avatar-Zustände im Assistenten (Denken, Lesen,
  Erfolg, Fehler) verbessern das Benutzererlebnis.
- **Automatische Dokumentation:** Der Builder erzeugt jetzt automatisch eine `Model_Card.md` mit SHA256-Hashes
  und einer Gebrauchsanleitung, gebündelt im ZIP „Golden Artifact“.

### Geändert
- **Hardware-Probe:** `hardware_probe.sh` und `.ps1` stark erweitert, um die Flags AVX512-VNNI und AMX zu
  erkennen, die für die Intel-Optimierung wesentlich sind.
- **GUI:** Schaltfläche „Deploy“ im Hauptfenster (aktiv nach einem erfolgreichen Build) und ein sicherer
  `DeploymentDialog`, der Zugangsdaten nur im RAM hält.
- **Builder:** Behandlung der Artefakte aktualisiert, um Modelldateien deterministisch zu erkennen und ein
  standardisiertes ZIP-Paket („Golden Artifact“) zu erzeugen.

## [1.6.0] - 2025-12-06
**Deep-Ingest-Release.** Befähigt den KI-Agenten „Ditto“, externe Dokumentations-Websites und PDFs rekursiv zu
crawlen, zu filtern und daraus zu lernen.

### Hinzugefügt
- **Deep-Crawler-Engine:** Integrierter rekursiver URL-Loader auf Basis von `LangChain`, um ganze
  Dokumentations-Websites zu verarbeiten (nicht nur einzelne Seiten).
- **PDF-Intelligenz:** Natives Einlesen von PDFs mit `pypdf`, damit Ditto Datenblätter und technische Handbücher
  lesen kann.
- **Intelligentes Filtern:** Spracherkennung (nicht englische Inhalte werden ignoriert) und Entfernen von
  Navigation und Fußzeilen für saubereres Wissen.
- **Wissens-Snapshots:** Der Modulgenerator extrahiert gelerntes Wissen jetzt automatisch aus Qdrant und bündelt
  es als `knowledge.json` im Target-Modul, damit es geteilt werden kann.
- **GUI-Dialoge:** Neuer `URLInputDialog`, um Dokumentations-Links gesammelt hinzuzufügen, mit Einstellungen für
  Crawl-Tiefe und Seitenlimit.
- **Rechtliche Vorgaben:** Verpflichtende Beachtung von „Robots.txt“ und ein Kontrollkästchen mit Haftungshinweis
  vor dem Crawlen.

### Geändert
- **Ablauf im Assistenten:** Schritt „Deep Ingest“ mit Live-Fortschrittsprotokoll in den Assistenten zur
  Modulerstellung integriert.
- **RAG-Manager:** `ingest_url` erweitert, um Ergebnisse des rekursiven Crawlens und PDF-Streams zu verarbeiten.
- **Config-Manager:** Einstellungen für `crawler_max_depth`, `crawler_max_pages` und den Eingabeverlauf
  hinzugefügt.

## [1.5.0] - 2025-12-01
**Expert-Knowledge-Release.** Macht den KI-Agenten „Ditto“ mit lokaler RAG-Technik von einem passiven Leser zu
einem aktiven Expertensystem.

### Hinzugefügt
- **Lokales RAG (experimentell):** Einbindung der Vektordatenbank Qdrant, um semantische Suche über
  Hardware-Dokumentation statt naivem Scraping zu ermöglichen.
- **Dynamische Sidecar-Architektur:** Qdrant läuft als Container bei Bedarf. Er wird nur heruntergeladen und
  gestartet, wenn der Benutzer die „Knowledge Base“ ausdrücklich einschaltet, um Ressourcen zu sparen.
- **Community Brain:** Neuer Ablauf, um indexierte Wissens-Snapshots über Git zu teilen. Benutzer können von der
  Community kuratierte Wissenspakete (`community/knowledge/*.json`) für Wissen ohne Internetverbindung
  importieren.
- **RAGManager:** Neue Kernkomponente (`orchestrator/Core/rag_manager.py`) für das Einlesen von Dokumenten, das
  Zerlegen in Abschnitte (Chunking) und die Vektorsuche.

### Geändert
- **Ditto-Agent:** Logik von `_fetch_documentation` aktualisiert, damit Ergebnisse der Vektorsuche Vorrang vor dem
  Herunterladen roher URLs haben, für mehr Präzision bei der Modulerzeugung.
- **DockerManager:** Logik ergänzt, um den Container `llm-qdrant` je nach Benutzerkonfiguration dynamisch zu
  starten und zu stoppen.
- **GUI:** Schalter „Enable Local Knowledge Base“ im Dialog der KI-Einstellungen hinzugefügt.

### Sicherheit
- **Datenschutz:** Vektordaten bleiben streng lokal (127.0.0.1). Es wird kein externer Vektorspeicher in der Cloud
  verwendet.
- **Bereinigung:** Export-Werkzeuge für Community-Wissen entfernen automatisch mögliche sensible Pfade oder
  API-Schlüssel, bevor Snapshots erstellt werden.

## [1.4.0] - 2025-11-30
**Smart-Calibration-Release.** Führt eine kontextbewusste Quantisierungs-Pipeline mit Human-in-the-Loop-Prüfung
ein, um auf NPU-Targets eine Modellqualität auf Enterprise-Niveau sicherzustellen.

### Hinzugefügt
- **Smart Calibration:** Automatischer Ablauf, der erkennt, ob ein Modell Kalibrierungsdaten braucht (z. B. INT8),
  und sie erzeugt oder auswählt.
- **DatasetManager:** Neue Kernkomponente (`orchestrator/Core/dataset_manager.py`) für deterministische
  Domänenerkennung und die Prüfung von Datensätzen.
- **Human-in-the-Loop:** `DatasetReviewDialog` hinzugefügt, damit Benutzer KI-erzeugte Kalibrierungsdaten vor dem
  Build prüfen und bearbeiten können.
- **Ditto-KI-Upgrade:** Dittos Fähigkeiten erweitert, synthetische domänenspezifische Trainingsdaten (Code, Chat,
  Medizin) auf Basis des SSOT-Wissens zu erzeugen.

### Geändert
- **Build-Pipeline:** `builder.py` und `docker_manager.py` aktualisiert, um Kalibrierungsdatensätze
  (`dataset.json`) in den Build-Container einzubringen.
- **Rockchip-Module:** `rkllm_module.sh` und `rknn_module.sh` (und ihre Python-Wrapper) aktualisiert, damit sie
  externe Kalibrierungsdatensätze annehmen und für eine genauere Quantisierung nutzen.
- **GUI:** Das `MainWindow` fragt jetzt gezielt nach Datensätzen, wenn empfindliche Quantisierungsmodi
  (INT8/W8A8) gewählt werden.
- **Richtlinie:** Heuristisches Raten von Modell-Domänen entfernt; stattdessen strenge Metadatenprüfungen oder
  Rückfragen beim Benutzer.

## [1.3.0] - 2025-11-29
**Production-Ready-Release.** Konzentriert sich auf vollständige Internationalisierung, Sicherheitshärtung und
darauf, den Kreis zwischen GUI und Build-Engine zu schließen.

### Hinzugefügt
- **I18n-Unterstützung:** Vollständige englische und deutsche Lokalisierung für GUI und Assistent.
- **LocalizationManager:** Zentraler Dienst für den dynamischen Sprachwechsel
  (`orchestrator/utils/localization.py`).
- **GUI-Erweiterungen:**
    - Neue Auswahllisten für **Task** (LLM, Voice, VLM) und **Quantisierung** (INT8, INT4, FP16).
    - Kontrollkästchen **„Use GPU“** für NVIDIA-Passthrough hinzugefügt.
    - Dialog zur Sprachauswahl beim ersten Start integriert.
    - **SecretsManager:** AES-256-Verschlüsselung für sensible API-Schlüssel.

### Geändert
- **Refactoring:** Textressourcen im `LocalizationManager` zentralisiert.
- **Abhängigkeiten:** `litellm`, `rich`, `psutil` und `cryptography` zu den Kernabhängigkeiten hinzugefügt.
- **Docker:** Statische Builder-Dienste zugunsten dynamisch gestarteter Container entfernt.

## [1.2.0] - 2025-11-29
**AI-Integration-Release.** Führt den Ditto-Agenten für automatische Hardware-Erkennung und Target-Erzeugung ein.

### Hinzugefügt
- **Ditto-KI-Agent:** Automatische Analyse von Hardware-Probes und Abruf von Dokumentation aus dem SSOT.
- **Smart Dispatcher:** Dynamische `build.sh`-Vorlage, die nach Task und Hardware verzweigt.
- **Vorlagen:** Vorlagen `rkllm_module` und `rknn_module` für die Unterstützung der Rockchip-NPU hinzugefügt.
- **Sicherheit:** `trivy-infra-scanner` für die automatische Schwachstellenprüfung der Container hinzugefügt.
- **NetworkGuard:** Robuste Verbindungsprüfungen und sichere Downloads.

### Geändert
- **Hardware-Probe:** Erkennung unter Windows über die native API (C#-Injection) gehärtet, für zuverlässige
  CPU-Flags.
- **Pipeline:** Erzeugungslogik in Python von den Bash-Skripten entkoppelt.

## [1.1.0] - 2025-11-28
**Architektur-Refactoring.** Entkoppelt den Kern-Builder von targetspezifischer Logik, um universelle
Cross-Kompilierung zu ermöglichen.

### Geändert
- **Architektur:** Fest codierte `TargetArch`-Enums entfernt; der Builder ist jetzt unabhängig vom Target.
- **Clean Code:** Eingebetteten Python-Code aus Bash-Skripten in eigenständige Dateien ausgelagert.
- **Builder:** Auf dynamisches Einhängen von Volumes für die Modulausführung umgestellt.

### Behoben
- **Stabilität:** Typfehler mit list/set im Windows-Installer behoben.
- **Cross-Kompilierung:** Fehlende Cross-Compiler (`gcc-aarch64`) in den Docker-Vorlagen ergänzt.

## [1.0.0] - 2025-11-20
**Erstes Release.** Legt die Grundstruktur des Frameworks und eine grundlegende Rockchip-Unterstützung an.

### Hinzugefügt
- Grundstruktur des Frameworks (Orchestrator, Builder, Docker-Manager).
- Grundlegende Unterstützung für die Cross-Kompilierung für Rockchip RK3588/RK3566.
- Einstiegspunkte für CLI (`llm-cli`) und GUI (`llm-gui`).
