# Changelog

Alle nennenswerten Änderungen an diesem Projekt stehen in dieser Datei.
Englische Fassung: [CHANGELOG.md](CHANGELOG.md).

Das Format basiert auf [Keep a Changelog 1.1.0](https://keepachangelog.com/de/1.1.0/),
und dieses Projekt hält sich an [Semantic Versioning 2.0.0](https://semver.org/lang/de/spec/v2.0.0.html).
Geplante Arbeiten stehen in der [Roadmap](docs/BLUEPRINT_DE.md#roadmap), nicht hier.

## [Unreleased]

### Hinzugefügt
- **`llm-cli models take-over`: die Übernahme alter Hugging-Face-Downloads im Terminal** (Konsolidierungskette
  hf-online): Installationen ohne GUI konnten nicht übernehmen, was frühere Versionen des Hugging-Face-Browsers der
  GUI im Cache von huggingface_hub im Modellordner gespeichert haben. Der Befehl ruft im Kern dasselbe auf wie **Alte
  Downloads übernehmen…** in **Tools > Hugging Face Model Browser...** (`ModelManager.old_downloads`, `take_over`,
  `old_copies_plan`, `delete_old_copies`): die vollständige Liste der Dateien, die noch zu übernehmen sind, die
  Ja-Frage mit „Nein“ als Vorgabe, die Kopie (jede Datei geprüft, unter der einen Speicherplatz-Regel; Strg+C hält
  zwischen zwei Blöcken an), jede Datei, die bleibt, mit ihrem Grund, danach die vollständige Liste der alten Kopien,
  die gehen können, Papierkorb oder endgültig, und die Löschung erst nach deinem Ja („Nein“ ist die Vorgabe), vorher
  protokolliert. Ohne Terminal wird nichts kopiert und nichts gelöscht. Scheitert die Kopie (eine Platte, die die
  Kopien nicht fasst, eine Datei, die ein anderes `llm-cli` oder die GUI gerade nutzt), endet der Befehl mit einer
  klaren Zeile „Es wurde nichts übernommen: ...“ mit dem Grund, wie das Fenster sie zeigt, nie mit einem Traceback.
  Der GUI-Paritätstest nennt seinen Weg mit der Maus (`orchestrator/gui/menus/existing.py`: `btn_hf_take_over` im
  Browser). Tests: `tests/test_hf_takeover.py` `TestTheCommandLinesTakeOver`.
- **Adresse und Host-Schlüssel eines vertrauten Boards kannst du ändern** (DECISIONS Runde 10 Q5): `llm-cli deploy
  devices edit GERÄT [--host] [--port] [--key "SCHLÜSSELZEILE" | --read-key]` und **Adresse oder Schlüssel ändern...**
  in **Boards > Boards und Geräte...** ändern Hostname oder IP-Adresse, SSH-Port oder Host-Schlüssel des Boards in der
  `known_hosts` des Frameworks, zum Beispiel nachdem sich seine Adresse geändert hat oder es neu installiert wurde.
  Niemand tippt einen Schlüssel ab: Er wird eingefügt (die Zeile aus `/etc/ssh/ssh_host_ed25519_key.pub` auf dem
  Board, eine `known_hosts`-Zeile oder eine Zeile von `ssh-keyscan`) oder vom Board unter seiner neuen Adresse
  gelesen; eine Zeile mit Optionen aus `authorized_keys`, Befehlen oder einem zweiten Schlüssel wird abgelehnt. Du
  siehst Adresse, Host-Schlüssel und Geräte-ID jetzt und nach der Änderung nebeneinander (im Fenster) und
  antwortest mit "Nein" als Vorgabe; die Änderung kommt ins Audit-Log, bevor sich etwas ändert, und wird abgelehnt,
  wenn sich die `known_hosts` inzwischen geändert hat. Ein neuer Schlüssel gibt dem Board eine neue Geräte-ID: Sein
  Eintrag zieht mit Name und Benutzer dorthin, und ob sich der Schlüssel des Frameworks anmeldet, wird einmal
  getestet; der Schlüssel eines anderen vertrauten Boards wird abgelehnt und dieses Board genannt, sodass sein Eintrag
  nie überschrieben wird. Die Daten des Boards ziehen mit seinem neuen Schlüssel um (DECISIONS Runde 22, P6;
  `orchestrator/runtime/device/board_move.py`): seine gespeicherte Prüfung, die Deploy-Einstellungen, die deployte
  Laufzeit (der Eintrag des letzten Deployments mit seinen Prüfungen und seinem Benchmark und seine Laufzeit-Pakete),
  der Deploy-Eintrag und seine Benchmark-Ergebnisse bei den Builds ("getestet auf"). Jeder Teil wird zur neuen
  Geräte-ID kopiert und geprüft (zurückgelesen, die Laufzeit-Pakete als harte Links oder als Kopien mit derselben
  sha256), bevor sich die `known_hosts` ändert, und die Kopien der alten ID gehen erst nach der Änderung; scheitert
  eine Kopie, wird die Änderung abgelehnt (kein Audit-Eintrag, `known_hosts` inzwischen geändert) oder endet sie auf
  andere Weise, bevor sich die `known_hosts` ändert (ihre Sperre hält ein anderer Prozess, ein Strg+C), gehen die
  Kopien wieder und nichts hat sich geändert. Sobald die `known_hosts` den neuen Schlüssel hält, gehen die alten Kopien
  auch dann, wenn ein Strg+C den Anmeldetest danach beendet, sodass die Daten des Boards nie doppelt bleiben. Was die
  neue ID schon mit anderem Inhalt hat, wird nie überschrieben (es bleibt unter der alten ID; die Prüfung und ihr Name
  und ihre Adresse ziehen gemeinsam um oder bleiben gemeinsam), eine alte Kopie, die sich nicht entfernen lässt,
  bleibt ebenfalls, und CLI und Fenster nennen beides, auch wenn der Eintrag des Boards nicht geschrieben werden
  konnte; beide sagen auch, ein neu eingerichtetes Board erneut zu prüfen (seine umgezogene Prüfung kann alt sein).
  Die Arbeit läuft im Hintergrund; die Texte gibt es auf Englisch und Deutsch. Kein Tool, der MCP-Server,
  Ditto oder die Laufzeit-Schritte für Geräte erreichen sie (`orchestrator/remote/pairing.py` `replace_key`, ein
  Test über den Importgraphen).
- **Nächste Schritte statt Befehlen, Befehle zum Zulassen und übersetzte, neu geordnete Menüs** (Welle 6, Kette
  a0; DECISIONS Runde 10 V1, V2, Q2, Q7): Der Qt-freie Katalog `orchestrator/utils/next_steps/` enthält jeden nächsten
  Schritt, den das Audit der Welle 6 nennt (104 Schritte in neun Bereichsmodulen, die von selbst gefunden werden,
  Texte auf Englisch und Deutsch), und ein Renderer macht aus einem Schritt den Befehl für die Kommandozeile
  (`Next: ... llmcf ...`), den Menüpfad in der Sprache der Person für das Fenster (`StepsRow` mit Öffnen, Kopieren für
  einen Befehl auf dem Board, mit Administratorrechten oder eines anderen Programms, Seite öffnen) und das Tool oder
  den Ort für eine KI (`ToolContext.channel`: eine KI, die für das Fenster arbeitet, bekommt nie einen Befehl).
  `proposal()` baut aus einem Schritt des Katalogs und geprüften Werten einen Befehl zum Zulassen (der genaue Befehl,
  was er tut und warum, übersetzt), `run_proposal()` führt genau diesen Befehl mit dem eigenen Python des Frameworks aus und reicht
  seine Zeilen weiter (Löschungen öffnen die Löschliste, Geheimnisse gehen über die Standardeingabe, andere Orte werden
  nur gezeigt, jede Entscheidung kann protokolliert werden; jeder Wert jedes Vorschlags wird nach seiner Art geprüft,
  ein Vorschlag, der in einem Wort, das die Person gelesen hat, von dem des Katalogs abweicht, wird abgelehnt, nur
  Befehle, die ohne Terminal durchlaufen und nicht mehr tun, als ihre Frage sagt, sind ausführbar, und eine KI liest
  aus einem Schritt nie das Login eines Boards oder einen Pfad); `error_report()` gibt der Fehlerbox eine übersetzte
  Zusammenfassung, den nächsten Schritt und das englische technische Detail. Träger: `ToolError(steps=...)`, an der
  Grenze der Registry für den Kanal des Aufrufers dargestellt, `BlockerSpec.step`, `WithSteps` an den Kern-Ausnahmen,
  `Line`, `Finding.steps`, `_say_error` in der CLI. Die Menüs: die KI-Verwaltung im Menü Ditto („Ditto einrichten..."
  zuerst), Laufzeit, Modelle und Bewertung unter Ditto > Erweitert, Extras mit Wissen und Wartung; übersetzte Titel
  mit Tastenkürzeln, jeder Eintrag dort benannt und beschriftet, wo er entsteht, eine Layout-Tabelle,
  `retranslate_menus()`. Die von Hand geschriebenen Menüpfade der Texte (30 Schlüssel auf Englisch und Deutsch) sind
  jetzt `{path:<objectName>}` und werden aus den Beschriftungen auf dem Bildschirm gefüllt; `tests/test_no_command_texts.py`
  bewacht die Texte samt ihrer Sie-Form und zählt den Rest gegen eine Ausgangslage, die nur sinken darf. Die
  deutschen Texte des Katalogs folgen der gewählten Anrede: Die Tabelle `FORMAL` eines Moduls hält die Sie-Form jedes
  Textes, der anders lautet.
- **Was Ditto und KI-Apps tun, wird zu Einträgen des Chat-Verlaufs, in jedem Prozess** (Welle 6, DECISIONS Runde 9
  U1, Folge b): `orchestrator/Core/activity.py` schreibt einen signierten Eintrag je Ereignis, aus dem Prozess, der die
  Arbeit getan hat (der GUI, `llm-cli`, jedem stdio-MCP-Server und dem HTTP-Kindprozess): ein Aufruf, der etwas
  geändert hat (das neue Registry-Ereignis `call_done`, Eintragsart `ai_action`), das Ende jeder Job-Art (ein
  Ende-Listener des Job-Dienstes), das Ende eines Builds (der Orchestrator; wer ihn abgebrochen hat, der gespeicherte
  Ordner als Bezeichnung), Nutzungen von Dauerfreigaben, ausstehende Freigaben und ihr Ende sowie abgelehnte Arbeit
  (`blocked`). Die eigenen Aufrufe der Person erzeugen keine Einträge; der Absender ist die vertrauenswürdige
  Aufrufer-ID, der eigene Name einer App nur ihre Angabe; Einträge enthalten nur geschlossene Werte und nennen das
  Fenster, das das Ergebnis zeigt (`orchestrator/tools/tool_windows.py`: die geschlossene Fensterliste, ein Fenster für
  jedes Werkzeug, das etwas ändert, geprüft von einem Test über die ganze Registry, für jede Job-Art, und was jedes
  Fenster öffnet), dazu die Schritt-IDs des Katalogs der nächsten Schritte (nie ein Textschlüssel, der wie eine
  aussieht); `refs.asked_in` markiert Dittos Runden im eigenen Chat der GUI. Er
  versagt offen, und dieselbe Ablehnung wird einmal in 30 Sekunden geschrieben. `ActivityProducer.add_listener` gibt
  der GUI die Einträge ihres eigenen Prozesses sofort, und `ConfigManager.reload_user_config` übernimmt Einstellungen,
  die ein anderer Prozess gespeichert hat, außer Werten, die dieser Prozess selbst hält (als Konflikte gemeldet). Die
  Seite der GUI (Fenster zeigen, Hinweise mit Zeigen) folgt in Welle 6.
- **Fachbegriffe beim Überfahren mit der Maus erklärt, ein verständliches Fehlerfenster und „du“ oder „Sie“ auf
  Deutsch** (Welle 6, DECISIONS Runde 10, V6, Q1, Q7): ein Glossar mit 74 Fachbegriffen
  (`orchestrator/utils/l10n_glossary.py`, zum Beispiel GGUF, Quantization und ihre Typen, Importance Matrix, KV Cache,
  RKLLM, RKNN, Qdrant, MCP, SSH Host Key, Fingerprint, Digest, Keyring), jeder mit einer kurzen Erklärung auf Englisch
  und Deutsch und einem Link auf die Seite, die ihn am besten erklärt (Wikipedia, die eigene Dokumentation des Begriffs
  wie qdrant.tech oder die llama.cpp-Doku, oder dieses Handbuch). Ruht die Maus auf einer markierten Beschriftung,
  zeigt eine Hover-Karte die Erklärung; sie bleibt, solange die Maus auf ihr ist, und ihr Link öffnet sich erst im
  Webbrowser, wenn du ihn anklickst. F1 im Feld öffnet dieselbe Karte für die Tastatur. Die Beschriftungen **Format**
  und **Quantization** im Builder sind die ersten; die übrigen Fenster folgen (ein Menüpfad in einem Text wird nicht
  nach Begriffen durchsucht). Ein neues Fehlerfenster sagt in deiner Sprache, was passiert ist, warum, und einen
  nächsten Schritt (einen Satz oder den Ort mit seinem Menüpfad und einem Knopf **Öffnen**), mit den englischen
  **Technischen Details** eingeklappt (ohne Geheimnisse und ohne deinen Home-Ordner), zum Kopieren in einen
  Fehlerbericht oder um Ditto zu fragen. Deutsche Texte sagen „du“; **Datei > Einstellungen…** hat jetzt einen Reiter
  **Allgemein**, in dem du stattdessen „Sie“ wählst (Einstellung `form_of_address`), sofort wirksam, auch für die
  nächsten Schritte. Größen im Löschdialog folgen deiner
  Sprache („1,5 GB“ auf Deutsch).
- **Aktionen für den Maintainer des Community-Repositorys** (Entscheidung vom 2026-10-22: die Arbeit des Maintainers
  als wenige Klicks statt einer Anleitung): **Tools > Community Hub öffnen > Maintainer** und
  `llm-cli zoo maintainer key|review|revoke`, eine Implementierung in `orchestrator/Core/community_maintainer.py`
  (`scripts/zoo_sign_index.py` ist jetzt eine dünne Hülle darüber). **Signaturschlüssel anlegen** (einmalig) legt den
  Signaturschlüssel des Index im Schlüsselbund des Betriebssystems an (ein Name, den es schon gibt, wird abgelehnt)
  und zeigt seinen öffentlichen Eintrag für `configs/community_keys.yml`, gespeichert als Textdatei, die du wählst.
  **Prüfen und veröffentlichen** listet die offenen Pull Requests von `community_repo`, die Pakete hinzufügen (und
  unter `contributions/` gemergte, noch nicht veröffentlichte Pakete), holt ein Paket vom Stand des Pull Requests,
  prüft es mit `zoo verify` und zeigt jede Datei (GUI: eine Dateiliste mit schreibgeschützter Textansicht; CLI:
  ausgegeben oder mit `--review-dir` in einen Ordner geschrieben, den du nennst; jedes Element der Paket-Datei), dann
  fragt es „Veröffentlichen?“ („Nein“ ist die Vorgabe); ein Pull Request, der außer neuen Paketen unter
  `contributions/<kind>/` noch etwas ändert oder in einen anderen Branch als `main` geht, wird nicht veröffentlicht,
  und die Prüfung nennt diese Änderung. Ja mergt den Beitrag (nur wenn seine Pflichtprüfung `validate` bestanden
  ist und sein
  Stand der geprüfte Commit ist), aktualisiert einen lokalen Klon im benutzereigenen Ordner, verschiebt das Paket nach
  `packs/<kind>/`, trägt es mit der Raw-Adresse in den Index ein, erhöht Version und Zeit des Index, signiert ihn mit
  dem Schlüssel aus dem Schlüsselbund, committet auf einem neuen Branch, pusht ihn, öffnet einen Pull Request, wartet
  auf die Pflichtprüfung und mergt ihn; nach `main` wird nie gepusht. **Veröffentlichtes Paket zurückziehen** trägt
  einen Rückzug (eine Datei, eine Version oder jede Version, mit dem Grund) auf demselben Weg ein. Jeder Schritt wird
  gemeldet und ins Audit-Log geschrieben, Abbrechen (Strg+C im Terminal) hält bis zum letzten Merge vor dem nächsten
  Schritt an (nach dem Push schließt es den eigenen Pull Request der Veröffentlichung und löscht seinen Branch, damit
  nichts halb Veröffentlichtes bleibt), und eine Ablehnung sagt, was bis dahin geschehen ist, und nennt den nächsten
  Schritt (kein `gh`, nicht angemeldet, offline, eine nicht bestandene Prüfung, ein abgelehntes Paket, ein Klon mit eigenen Änderungen). Der
  Index auf `main` muss sich mit deinem Schlüssel oder einem vertrauenswürdigen Schlüssel prüfen lassen, bevor etwas
  darauf signiert wird. gh und git nutzen dein eigenes Login; das Framework liest, speichert und zeigt nie einen
  Token. Nur die Person: kein MCP-Tool, Ditto oder anderer Weg einer KI erreicht es (Test als Absicherung). Die
  Vorlage des Prüf-Workflows `scripts/community_repo/validate-contribution.yml` läuft jetzt bei jedem Pull Request
  (kein Pfadfilter; ein Pull Request ohne Paket besteht), wie die Kopie im Community-Repository, weil dessen
  geschützter `main` sie verlangt. Seine Meldungen nennen einen Schritt des Frameworks im Terminal als Befehl und im
  Fenster als Ort (`hub.refresh`, `maint.review` und `audit.verify` des Katalogs der nächsten Schritte, die
  Einstellungen mit ihrem Menüpfad), im Fenster nie einen Befehl; der Knopf **Aktualisieren** des Community Hubs ist
  jetzt übersetzt.
- **`tests/test_cli_gui_parity.py`: Jeder Befehl und jede Option der CLI für Personen hat einen Weg mit der Maus**
  (W1', Runde 9 U2): Der Test geht den ganzen Befehlsbaum von `llm-cli` durch (294 Pfade: Befehle, Unterbefehle und
  Optionen) und liest `CLI_PATHS`, `MACHINE_ONLY` und die Fenster- und Reitertabellen jedes Moduls in
  `orchestrator/gui/menus/`. Er schlägt fehl, wenn ein Befehl oder eine Option weder einen Weg mit der Maus noch einen
  Grund als reine Maschinenausgabe hat, wenn eine Tabelle einen Befehl nennt, den es nicht gibt oder den zwei Module
  beanspruchen, und in einem Hauptfenster offscreen, wenn ein genanntes Objekt fehlt oder eine Menüaktion von der
  Menüleiste aus nicht erreichbar ist (jedes Fenster wird über seinen Menüeintrag geöffnet; Objekte in Fenstern und
  Reitern werden in dem Fenster oder Reiter geprüft, den ihr Eintrag öffnet). Heute haben 238 Pfade einen Weg mit der
  Maus, 37 sind mit Grund nur für Skripte, keiner fehlt. Die Wege, die es vor Welle 5 ohne Bereichsmodul gab
  (File > Trash..., `ditto setup`, `--version` unter Help > About), nennt `orchestrator/gui/menus/existing.py`; die
  Menüs aller Bereiche werden über ihre Objektnamen mit einer Hilfsfunktion gefunden
  (`orchestrator/gui/menus/place.py`), deshalb fügt ein übersetzter Menütitel nie ein zweites Menü hinzu.
  CONTRIBUTING nennt die Regel.
- **Tools > Mojo-Gesundheitscheck... in der GUI** (DECISIONS W1': jede Aktion der Kommandozeile auch mit der Maus):
  Das Mojo-Fenster startet Mojo Oracle beim Öffnen, außerhalb des GUI-Threads, und zeigt jeden Befund so, wie
  `llmcf mojo` ihn ausgibt (die Schwere als Text mit Zeichen und Symbol, nie nur als Farbe; wo mit dem vollen Pfad,
  warum es wichtig ist, den nächsten Schritt, was Mojo Miracle tun kann, mit den Schritten seines Plans; den
  zusammenfassenden Satz und eine Gesamtzeile mit dem Exit-Code). **Mojo Miracle ausführen...** fragt „Mojo Miracle
  jetzt ausführen?“ („Nein“ ist die Vorgabe) und lässt Mojo Miracle mit der GUI als deinem Kanal laufen: Die gezeigten
  Pläne binden die Reparaturen, die Build-Images des Frameworks öffnen sich im Löschdialog mit der ganzen Liste und
  nichts angehakt, und das Fenster zeigt, was repariert wurde, was nicht und warum. Kein Board wird von sich aus
  kontaktiert: Ein Board, auf das ein aufgezeichneter Fakt zeigt, bekommt **<Board> jetzt prüfen**, und **Ein Board
  prüfen...** prüft ein beliebiges vertrautes Board, das du auswählst (an Bezeichnung und Adresse, und auch an der
  Geräte-ID, wo zwei Boards gleich aussehen; eine Verbindung zu seinem SSH-Port, keine Anmeldung). Befehl und Fenster teilen dieselben Kernfunktionen (`Core/mojo/boards.py`, die Sicht der Person in
  `Core/mojo/views.py`, `docker_objects.offer_image_list`), die Ausgabe von `llmcf mojo` bleibt also, wie sie war.
- **Dieses Board prüfen im Deployment-Tab** (Gruppe „Benchmark auf einem Board“, DECISIONS W1'): prüft das gewählte
  Board so, wie es `llmcf mojo --oracle --board` tut, im Hintergrund (eine Verbindung zu seinem SSH-Port, keine
  Anmeldung), und zeigt seinen Befund mit dem nächsten Schritt unter der Zeile; ein Problem wird auch in einer Meldung
  genannt.
- **Boards > Boards und Geräte...: jeder Befehl von `deploy devices` und `device` mit der Maus** (W1'): Ein neues
  Menü Boards öffnet ein Fenster für jedes Board, das du untersucht hast oder dem du vertraust, mit dem Konto auf
  dem Board, dem vollen Fingerabdruck des Host-Schlüssels und seinem Stand in `known_hosts`, seit wann ihm vertraut
  wird, dem letzten Deployment, seinem System, den Deploy-Einstellungen und der installierten Laufzeit. Seine Knöpfe
  rufen dieselben Kernfunktionen wie die CLI auf (neu `runtime/device/person_steps.py` und `steps.py`; die CLI ruft
  sie auch auf, ihre Ausgabe bleibt gleich), mit denselben Prüfungen, Ablehnungen, Fragen ("Nein" ist die Vorgabe)
  und Audit-Einträgen: Vertrautes Board hinzufügen..., Vertrauen entfernen... (bisher konnte nur die CLI einem
  Board die Erlaubnis für KI-Deployments wieder nehmen), Einstellungen... (mit der sudo-Frage für systemd),
  Laufzeit-Plan, Status, Laufzeit bauen... (Jobs, Spiegel, die Frage nach dem Quelltext, Abbrechen), Laufzeit
  installieren... (das Passwort jedes Mal abgefragt, nie gespeichert; ein nicht vertrauenswürdiges Benchmark-Modell
  nur nach einer ausdrücklichen Warnung), Prüfen... (auch ohne Modell), Ältere Laufzeit-Pakete entfernen... und
  Ältere Builds auf dem Board entfernen... (der Löschdialog mit der vollständigen Liste) sowie Dieses Board prüfen
  (Mojo). Jeder Kontakt mit einem Board und jeder Build laufen im Hintergrund. Die Laufzeit-Tools der KI bauen und
  installieren mit denselben Schritten. Beschädigte Deploy-Einstellungen ("damaged; set them again") lassen sich jetzt
  neu festlegen, im Fenster und mit `device settings`, nach einer Frage, die die Datei mit vollem Pfad und Größe nennt.
- **Tools > Tool Permissions (Ditto / MCP)...: jede Option von `tools list` und `tools permissions` per Maus** (W1'):
  eine Spalte „Gilt jetzt“ zeigt die Entscheidung jedes Tools mit ihrem Grund (wie `tools list` sie ausgibt), und ein
  Hinweis nennt die Aufrufer mit eigenen Berechtigungen; „Wessen Berechtigungen“ zeigt und bearbeitet die gemeinsamen
  Berechtigungen oder den eigenen Abschnitt von Ditto oder den lokalen MCP-Clients (`--caller`); „Auf die empfohlenen
  Standards zurücksetzen...“ (`--reset`), „Wieder die gemeinsamen Berechtigungen nutzen...“ (`--reset --caller`) und
  „Zurück auf nur lesen...“ für ein Token (`--reset --token`: wieder ein neues Token, keins mit von Hand gesetztem
  Allow/Deny) nennen, was sich ändert, und fragen zuerst („Nein“ ist vorgewählt; eine inzwischen woanders
  gespeicherte Änderung wird abgelehnt). Ein neues Token kann als Kopie der eigenen Berechtigungen eines anderen
  Tokens beginnen (`mcp token add --policy-from TOKEN`). CLI und GUI teilen sich die Kernfunktionen
  (`policy.with_section`, `reset_policy`, `reset_changes`, `mcp_tokens.add_token`, `copied_policy`,
  `set_token_policy`, `reset_token_policy`, `revoke_token`); die Ausgabe der CLI bleibt gleich.
- **Security > Prüfprotokoll...: jede Option von `tools audit` per Maus** (W1'): die Zeilen des Audit-Logs, wie die
  CLI sie zeigt (gemeinsames `tools/audit_view.py`), Seite für Seite mit „Einträge pro Seite“, „Neuere“ und
  „Ältere“; „Details“ (`--details`); „Unversehrtheit prüfen“ (`--verify`) mit dem Ergebnis in einfachen Worten;
  „Archivieren...“ (`--archive`) nach einer Rückfrage, nichts wird gelöscht; und der Reiter „Übersicht je Aufrufer“
  (`--summary`). Prüfung, Archivieren und Übersicht laufen im Hintergrund. `AuditLog.entries` nimmt einen Versatz,
  und `AuditLog.count` nennt die Größe, zum Blättern.
- **Security > Verbindungen zu KI-Apps (MCP)...: jede Aktion von `mcp config`, `mcp serve --transport http` und
  `mcp token` per Maus** (W1'): die Einträge für Claude Code, Claude Desktop (mit dem Pfad seiner
  Konfigurationsdatei) und VS Code, jeder mit einer Schaltfläche zum Kopieren; „In Claude Desktop eintragen...“ zeigt
  die Datei, ob es sie gibt, die Änderung, die Einträge, die bleiben, und die Sicherung, fragt zuerst und schreibt
  genau das (`mcp_server.plan_claude_desktop_config`, eine inzwischen geänderte Datei wird abgelehnt); der lokale
  HTTP-Server (Adresse, Port, Container-Bindung) als Kindprozess der GUI mit dem eigenen Einstieg und den Ablehnungen
  der CLI, Adresse, Zustand und Ausgabe zu sehen, gestoppt auf Wunsch oder wenn das Framework nach einer Rückfrage
  schließt („Nein“ lässt es offen), indem seine Eingabe geschlossen wird; so endet er auf jedem System wie bei
  Strg+C, auch unter Windows ohne Konsole (`mcp_server.stop_when_input_ends`; Strg+Pause oder SIGTERM folgt erst
  nach 5 Sekunden, ein sofortiges Ende nach 15); und die Liste der Tokens (Hinzufügen, auch als Kopie der Berechtigungen eines
  anderen Tokens, Rotieren, Widerrufen, Zurück auf nur lesen, Berechtigungen). Ein Token wird einmal gezeigt, direkt
  nachdem es angelegt oder rotiert wurde.
- **Tools > Hinweis zu Deep Ingest... und die Art eines KI-Ordners** (W1'): der Deep-Ingest-Hinweis hat einen eigenen
  Eintrag und lässt sich jederzeit lesen (`tools crawl-notice`; bestätigt auf das Ja der Person, „Nein“ ist
  vorgewählt, mit der Funktion der CLI `crawl_notice.acknowledge_and_audit`); der Modul-Assistent zeigt ihn vor der
  Prüfung der Wissensdatenbank. Tools > Folders the AI May Use... > Add folder... fragt, was im Ordner liegt
  (Modell-Dateien, Datensatz-Dateien oder beides: `tools folders add --kind`).
- **Security > Anmeldeübersicht...: jeder Weg von `ditto login`, `ditto logout` und `ditto auth status|methods` mit
  der Maus** (W1'): Ein Fenster zeigt die Anmeldung aller Anbieter auf einmal (Verfahren, Quelle, Konto oder die
  letzten vier Zeichen eines Schlüssels, Zustand, Dittos Anbieter markiert) und wie der Tresor geschützt ist, mit
  seiner Datei; **Alle prüfen** führt im Hintergrund die kostenlose Prüfung jedes Anbieters mit Zugangsdaten aus
  (`--check`); der Reiter **Alle Anmeldeverfahren** listet die Verfahren jedes Anbieters mit ihren
  Umgebungsvariablen und Anmerkungen. Wer einen Anbieter wählt, sieht sein Anmeldefeld mit einem eigenen Feld
  **Serveradresse**: Eine dort eingegebene (oder von den KI-Einstellungen übergebene) Adresse wird nur für diesen
  Anbieter gemerkt und nicht mehr nur über die KI-Einstellungen, die den Anbieter dabei auch zu Dittos machten
  (`login --base-url`). Die Schlüsselkarte nennt die Umgebungsvariablen und die Anmerkung des Verfahrens und bietet
  **Den Schlüssel aus <VARIABLE> im Tresor speichern** an, solange eine gesetzt ist (`login --from-env`, vorher
  geprüft, außer du nimmst den Haken weg; neu `SignIn.env_variable()` / `SignIn.env_key()`, die auch die CLI
  aufruft); ein Server ohne Anmeldung (Ollama, `custom` ohne Schlüssel) hat **Vergessen** (`logout`: die gemerkte
  Adresse und das gemerkte Verfahren). Vor dem Abmelden oder Vergessen fragt es („Nein“ ist vorgewählt). Das
  Framework liest, speichert und übergibt nie Zugangsdaten eines Claude-Abos und startet nie den Befehl claude.
- **Security > Schlüsselspeicher...: `ditto auth storage` und `ditto auth rotate-key` mit der Maus** (W1'): wie der
  Tresor geschützt ist und seine Datei, auch während er funktioniert (bisher zeigte die GUI das nur, wenn er
  scheiterte); **Anders schützen** wechselt zwischen dem Schlüsselbund des Betriebssystems (neu als Wahl in der GUI,
  so braucht der Weg zurück zum Schlüsselbund die CLI nicht mehr), einem Credential-Helper mit seinem Befehl, einer
  Passphrase und der unverschlüsselten Schlüsseldatei (nach der Bestätigung und der Frage der CLI, „Nein“ ist
  vorgewählt); **Schlüssel erneuern...** fragt vorher („Nein“ ist vorgewählt). Der Tresor wird im Hintergrund mit
  denselben Kernfunktionen wie in der CLI neu verschlüsselt.
- **Security > Geheimnisse...: `secrets list` und `secrets set` mit der Maus** (W1'): die Namen, die das Framework
  liest (`hf_token`, `qdrant_api_key`, die Schlüssel der Community-Pakete), und jeder gespeicherte Name mit
  „gespeichert“ oder „nicht gespeichert“ und wofür, nie ein Wert; **Ein Geheimnis setzen** nimmt einen Namen aus der
  Liste oder einen anderen Namen und den Wert zweimal in verdeckten Feldern. CLI und Fenster rufen dasselbe neue
  `Core/secret_entries.py` auf (`store_secret`, `secret_entries`; die Ausgabe der CLI bleibt gleich): Im Speicher
  `ephemeral` sagen beide, dass nichts gespeichert wurde, und ebenso Security > Set OpenAI API Key, das dort
  „gespeichert“ meldete.
- **Tab Deployment: jede Option von `deploy package` und `deploy run` mit der Maus** (W1', GUI-Parität): **Nur Paket
  erstellen…** erstellt das Paket des ausgewählten Builds für das gewählte Profil, ohne ein Board zu kontaktieren, mit
  Docker-Images für ein Board ohne Netz, wenn du Docker ankreuzt, und **Anderer Build oder Modelldatei…** fügt der
  Build-Liste jeden Build-Ordner und jede Modelldatei hinzu (auch die CLI nimmt jeden Pfad); der
  Ordnerdialog öffnet sich beim Ausgabeordner wie für jedes Golden Artifact, und die Meldung nennt den vollen Pfad des
  Pakets oder den Grund und den nächsten Schritt (`llm-cli deploy package`, derselbe Schritt im Kern). **Vorhandenes
  Paket deployen…** lädt ein früher erstelltes ZIP hoch (`deploy run PACKAGE_PATH`). Die neuen Felder **Port**,
  **Name** und **Auf das Board warten** sind `--port`, `--label` und `--wait` für beide Deploy-Schaltflächen. Der
  Host-Schlüssel des Boards wird jetzt im Hintergrund gelesen und sein Fingerabdruck mit „Nein“ als Vorgabe gezeigt; ein
  fehlgeschlagenes Paket bei **Deploy to Target** sagt, warum; die Erfolgsmeldung nennt den vollen Pfad des Pakets, wie
  oft das Deployment auf das Board gewartet hat, und den Namen des vertrauten Boards.
- **Tab Builder > Scan / Import...: jede Option von `llm-cli scan` mit der Maus** (W1', GUI-Parität): **Scan Local
  Host** führt jetzt den Schritt der CLI aus, die Python-Probe, und speichert das Ergebnis als Probe dieses Rechners
  (Probe-ID `local`, die KI-Clients und Ditto nennen), mit seinen Hinweisen und der Warnung „Probe noch einmal laufen
  lassen“ im Protokoll; **Lokalen Rechner mit dem Ersatz-Skript scannen** (`--script`), **Lokalen Rechner scannen
  und Profil speichern unter...** (`--output`; eine `.json`-Datei bekommt die JSON-Ansicht, `--json`) und beides
  zusammen sind neu.
  **Remote Scan via SSH...** bekommt die Felder **Port** und **Name** und schreibt wie `scan --device` den Eintrag des
  Boards als vertraut und gekoppelt (im Audit-Log), so dass das Board in der Benchmark-Liste erscheint und
  KI-Deployments es nennen können; CLI und GUI teilen einen Schritt im Kern (`scan_steps.scan_device`). **Import from
  File** fragt nach dem Namen des Boards (`--label`).
- **Build > Targets…** (GUI-Parität, jede CLI-Aktion mit der Maus): jedes Target mit seiner ID (dem Wert für
  `--target`), Name, Architektur, Boards, Formaten und Unterstützungsgrad, oder „keine Fähigkeiten angegeben“ (oder dass
  sein Fähigkeiten-Block ungültig ist), aus der Abfrage von `llm-cli targets list`. **Target prüfen** (dort und neben
  der Target-Liste des Builders, neben der neuen Schaltfläche **Targets…**) prüft das gewählte Target wie `llm-cli
  targets validate`, über eine Kernfunktion (`TargetManager.validate`, die jetzt auch die CLI aufruft), mit frisch
  gelesener `target.yml`, und nennt jeden Schemafehler mit seinem Feld. Dieselbe Prüfung läuft, nachdem der
  Modul-Assistent ein Target angelegt hat und nach der Installation eines Targets aus dem Community Hub, und ihr
  Ergebnis wird angezeigt.
- **Build > Build-Verlauf…**: jeder Build jedes Programms (GUI, CLI, MCP-Server), alle wie `llm-cli build status
  --all`, auch gescheiterte, abgebrochene, unterbrochene und wartende; **Details anzeigen** (oder ein Doppelklick)
  zeigt einen Build wie `build status REQUEST_ID`: seine Anfrage mit Fehlern und Hinweisen, jeden Versuch mit seinem
  Ausgabeordner (voller Pfad) und Fehler und die Korrekturvorschläge der KI-Clients, als KI-generiert markiert. CLI und
  Fenster lesen eine Abfrage (`orchestrator/Core/build_history.py`). Die Statusleiste nennt für Builds anderer
  Programme jetzt dieses Fenster statt eines Befehls.
- **Datei > Einstellungen…**: die wirksamen Einstellungen und woher jeder Wert kommt, Geheimnisse geschwärzt (wie
  `llm-cli config show`), und die Repository-Überschreibungen mit der Variablen, zu der jede wird, zum Hinzufügen und
  Entfernen (wie `llm-cli config repos`; Entfernen fragt zuerst, „Nein“ ist die Vorgabe). Beide nutzen ein Kernmodul
  (`orchestrator/Core/settings_view.py`), das jetzt auch die CLI aufruft; ein Speichern, das scheitert, lässt die
  Einstellung auch im laufenden Programm unverändert.
- **Der Builder bietet, was die CLI bietet**: Ein Target ohne Fähigkeiten bekommt jedes Format von `--format` (nicht
  nur GGUF, RKLLM und RKNN), mit dem Hinweis, dass seine Boards, Formate und Quantisierungen nicht geprüft werden (für
  ein Target mit ungültigem Fähigkeiten-Block sagt der Hinweis das, und **Target prüfen** nennt jeden Fehler); die
  Optimierungsstufe `FAST`; eine Liste **Priorität** (`--priority`, Vorgabe normal; ein fehlgeschlagener kritischer
  Build beendet die Pipeline).
- **Modul-Assistent: Beschreibung und Anbieter und Modell für eine Analyse**: Die Hardware-Seite hat ein Feld
  **Beschreibung** (`llm-cli module create` fragt danach; leer ergibt „Target for <Name>“). Nur du füllst es aus: Bei
  einem Modul, an dem eine KI mitgewirkt hat, steht deine Beschreibung vorn, und der Hinweis des Frameworks, dass das
  Modul KI-generiert und ungetestet ist, folgt. Die erste Seite bietet Anbieter und Modell **nur für diese Analyse**
  (`module generate-ai --provider --model`): Ditto läuft damit über `DittoManager.for_run`, das jetzt auch `generate-ai`
  in der CLI nutzt, und die gespeicherten KI-Einstellungen bleiben unverändert. `for_run` nutzt dieselbe Prüfung
  des Anbieters und dieselbe Überlagerung der Einstellungen wie die Wahl für eine Frage im Chat-Bereich und Ditto >
  Bewertung... (`orchestrator/Core/ai_override.py`): ein Code-Weg für jede solche Wahl.
- **Ditto > Lokale Laufzeit...: jeder Befehl `llm-cli ditto runtime` mit der Maus** (W1'): Ein Fenster zeigt, was
  `ditto runtime status` zeigt (Hardware, Betriebsart, der Build in Gebrauch und seine Prüfungen, Probleme und der Rat
  zum Neubau, der Plan für den eigenen Build, das fertige Archiv, der Server mit Adresse, Modell und Kontext, das
  empfohlene und das gewählte Modell, die Protokolldatei), und erledigt `install` (**llama-server bauen...** mit
  GPU-Backend, Ort des Builds und Compile-Jobs; **Offizielles fertiges Archiv installieren...** mit „Noch einmal
  herunterladen“), `ensure` (Betriebsart Automatisch, Direkt oder In Docker, dieselben Build-Optionen und das
  Modell-Angebot danach mit Größe und Lizenz, ohne Haken wie `--no-model`), **Starten** (Modellstufe oder die
  Empfehlung, Kontextlänge, Entladen im Leerlauf; das früher gewählte Modell wird genannt) und **Stoppen**, **Build
  importieren...** und **Build exportieren...**, **Ältere Builds entfernen...** (der Lösch-Dialog mit der ganzen
  Liste, nichts angehakt, „Nein“ als Vorgabe), **Quellcode exportieren...** (mit optionalem Spiegel) und **Quellcode
  importieren...**. Es laufen dieselben Kernfunktionen wie in der CLI (die Status-Übersicht, das Modell-Angebot und
  die llama.cpp-Festlegung sind jetzt Kernfunktionen, die auch die CLI aufruft; ihre Ausgabe bleibt gleich); Builds,
  Downloads, Docker und der Serverstart laufen im Hintergrund mit einer Fortschrittszeile und Abbrechen, wo sich der
  Befehl unterbrechen lässt; jede Frage der CLI wird mit denselben Worten und „Nein“ als Vorgabe gestellt (auch die
  vor dem Holen des festgelegten Quellcodes); in der ganzen GUI läuft immer nur eine Aufgabe der Laufzeit. Der Bereich
  „Dittos lokaler Server“ (hier und in den KI-Einstellungen) lehnt die installierte Art nicht mehr ab: **Prüfen und
  reparieren...** prüft sie und installiert sie nur neu, wenn sie sich nicht nutzen lässt (zum Beispiel ein alter
  llama.cpp-Build nach einem Update), und Installieren und Wechseln nehmen jetzt die Build-Optionen mit; der Wechsel
  behält die Platzprüfung und das vollständige Entfernen der alten Art. Sein neuer Knopf **Status und mehr...** öffnet
  das Fenster aus den KI-Einstellungen. Ein gestoppter lokaler Server und ein abgelehntes Holen des Quellcodes nennen
  jetzt neben dem Befehl auch den Weg in der GUI.
- **Tools > Knowledge Snapshots... sagt vorher, ob der nächste Snapshot passt, und zeigt den Speicher in jedem
  Zustand** (W1'): Die Zeile zur Festplatte sagt jetzt mit den Worten von `llm-cli knowledge snapshots`, wenn der
  nächste Snapshot (etwa so groß wie der neueste) nicht passen würde, sodass Deep Ingest, eine Wiederherstellung und ein
  Paket-Import abgelehnt würden, oder nur wenig Platz ließe. Die Zeile zum Speicher erscheint in jedem Zustand, so wie
  `llm-cli knowledge storage` ihn zeigt (Qdrant Edge mit seinem Ordner, der alte Speicher mit seinem Grund, ein
  Qdrant-Server, Qdrant Edge, das sich nicht laden lässt), und **Jetzt umwandeln** wird auch vor dem ersten Versuch
  angeboten, unter der Bedingung von `llm-cli knowledge storage convert`. CLI und Fenster rufen dieselben neuen
  Kernfunktionen auf (`knowledge_snapshots.snapshot_disk`, `migration.storage_status`, `storage_lines` und
  `convert_refusal`); die Ausgabe der CLI bleibt gleich, außer bei einem eingestellten Qdrant-Server (siehe Behoben).
- **Tools > Wissensquellen...: jede Quelle der Wissensbasis per Maus** (W1'): Ein neues Fenster listet jede Quelle mit
  ihrer Art, der Lizenz und dem Urheberrechtshinweis, die sie zeigt, und was ein Wissenspaket von ihr mitnähme (voller
  Text, Link und kurze Fakten, nicht exportiert) und warum, mit einem Filter nach Art (Webseiten, Seiten aus
  Wissenspaketen, Dokumente des Frameworks, sonstige). Es liest die Liste im Hintergrund über das neue Kernmodul
  `knowledge_sources`, das auch `llm-cli knowledge sources` nutzt, sodass beide dieselben Worte zeigen. Die Verdrahtung
  des Menüs liegt in `orchestrator/gui/menus/knowledge.py`, dessen `CLI_PATHS` den Mausweg jedes `knowledge`- und
  `zoo`-Befehls nennt.
- **Community Hub: jede Aktion von `llm-cli zoo` per Maus** (W1'): **Details...** (oder ein Doppelklick) zeigt, was
  `zoo show` zeigt (Lizenz, Vertrauensstufe, installierter Ordner, Download-Adresse, Größe, sha256, die `recipe.yml`
  eines installierten Rezepts); **Import file...** nimmt eine Passphrase (nie gespeichert oder angezeigt) und die
  Schlüsseldateien der einzigen Absender, die angenommen werden, wie `zoo install --file --passphrase / --from`;
  **Installierte Pakete prüfen** und **Paketdatei prüfen...** führen `zoo verify` ohne und mit Datei aus;
  **Exportieren...** exportiert jeden Target-Ordner (auch einen vorhandenen oder installierten), einen fertigen Build
  oder die Wissensbasis mit ihrer Paket-ID, offen oder an die Schlüssel in Schlüsseldateien bzw. mit einer Passphrase
  verschlüsselt, in einen Ordner deiner Wahl; **Meine öffentlichen Schlüssel...** zeigt `zoo keys` mit Kopieren und
  Speichern.... Jede ruft die Kernfunktion der CLI auf (neu: `CommunityManager.details`, `exportable_targets`,
  `read_public_key_files`, `needs_passphrase`), im Hintergrund; Fragen behalten „Nein“ als Vorgabe, und ein
  verschlüsseltes Paket wird nie eingereicht.
- **Ditto > Lokale Modelle...: `llm-cli ditto models list` und `download` mit der Maus** (W1'): jede Stufe von
  Dittos festgelegtem Katalog mit Datei, Größe, Lizenz, ob sie heruntergeladen (und geprüft) ist, und ihrem Hinweis
  (empfohlen für diesen Computer, gewählt, auf Wunsch, nicht festgelegt), dem Grund der Empfehlung und dem vollen
  Modellordner; **Empfohlenes Modell herunterladen...** oder **Ausgewähltes herunterladen...** nach der Frage der CLI
  (Repository, Datei, Lizenz, Größe, Ordner; „Nein“ als Vorgabe), im Hintergrund mit Fortschritt und **Abbrechen** (der
  Teil bleibt für die Fortsetzung). Der empfohlene Download nimmt die Empfehlung, die das Fenster zeigt (mit der Liste
  im Hintergrund gelesen), damit keine Hardware-Prüfung im GUI-Thread läuft. Die Zeilen des Katalogs und das Modell
  eines Downloads sind Kernfunktionen, die auch die CLI aufruft (ihre Ausgabe bleibt gleich). Die Installationsfrage
  der Laufzeit in den KI-Einstellungen nennt dieses Fenster statt des CLI-Befehls. Ein Download ist eine Aufgabe von
  Dittos lokaler Laufzeit: Er wartet, solange in Ditto > Lokale Laufzeit... oder in den KI-Einstellungen ein Build,
  ein Serverstart oder ein anderer Download läuft, und diese warten auf ihn.
- **Ditto > Bewertung...: `llm-cli ditto eval` mit der Maus** (W1'): Arten und Retrieval-Varianten (fused, rerank),
  einzelne Fälle, eine andere Falldatei, Läufe pro Fall, mehrere lokale Stufen, oder Anbieter, Modell und Denkaufwand
  nur für diesen Lauf (die gespeicherten KI-Einstellungen bleiben unverändert); eine Frage vor dem Lauf sagt, was
  aufgerufen wird und ob es Geld kostet, auch wenn die Ersatzkette der KI-Einstellungen einen Cloud-Anbieter nennt, und
  dass Retrieval seine Modelle bei der ersten Verwendung herunterlädt („Nein“ als Vorgabe); eine Stufe, die noch nicht
  heruntergeladen ist, wird mit einem Hinweis auf Ditto > Lokale Modelle... abgelehnt (ihr Start würde zuerst deinen
  Server stoppen); ein Lauf mit lokalen Stufen ist eine Aufgabe von Dittos lokaler Laufzeit und wartet, solange eine
  andere läuft; der Lauf geht im Hintergrund mit Fortschritt und **Abbrechen** (vor dem nächsten Versuch;
  Abbrechen während der Aufnahme lädt keinen Reranker), das Fenster zeigt die Tabellen der CLI, und **Bericht
  speichern...** schreibt ihren JSON-Bericht. Der Lauf selbst (Auswahl der Fälle, die Schleife über die Stufen mit dem
  Wiederherstellen des Servers, die Retrieval-Läufe, die Tabellen und der Bericht) liegt jetzt in
  `orchestrator/evals/eval_run.py`, das auch die CLI aufruft; ihre Ausgabe und Exit-Codes bleiben gleich, nur wird ein
  Server von dir, den eine Stufe ersetzt hat, jetzt auch gemeldet, wenn der Lauf früher endet (eine spätere Stufe, die
  in der CLI nicht startet, Abbrechen im Fenster).
- **Chat-Bereich: Optionen für eine Frage** (W1', `llm-cli ditto ask --no-tools/--provider/--model` mit der Maus):
  **Optionen für diese Frage** über dem Nachrichtenfeld bieten **Ohne Werkzeuge antworten**, einen Anbieter und ein
  Modell nur für die nächste Frage. Die gespeicherten KI-Einstellungen bleiben unverändert (die Frage geht an einen
  Ditto mit diesen Werten über den Einstellungen, `orchestrator/Core/ai_override.py`, das den Anbieter auch wie die CLI
  prüft), der Bereich sagt, was die Frage verwendet hat, und die Optionen werden nach dem Senden zurückgesetzt; Feed,
  Fragen und Freigaben arbeiten wie bisher.
- **`llm-cli knowledge storage` und der alte Speicher der Wissensbasis in der Löschliste** (DECISIONS RG5): `llm-cli
  knowledge storage` zeigt, wo die Wissensbasis liegt, wann eine ältere umgewandelt wurde (wie viele Auszüge, was
  geprüft wurde) oder warum ihre Umwandlung fehlschlug, und den alten Ordner mit vollem Pfad und Größe; es liest nur den
  Eintrag, und seine erste Zeile nennt den Speicher, der wirklich genutzt wird. `convert` und **Tools > Knowledge
  Snapshots... > Umwandlung erneut versuchen** in der GUI (im Hintergrund) versuchen die Umwandlung sofort erneut.
  `delete-old` und **Tools > Knowledge Snapshots... > Alten Speicher löschen...** in der GUI zeigen den alten Ordner
  nach seiner Umwandlung und löschen ihn erst nach deinem Ja (Papierkorb oder endgültig; "Nein" ist die Vorgabe; im
  Audit-Log als `old_store_deleted`); nichts löscht ihn von selbst, und eine KI kann es nie. Mojo Oracle meldet eine
  fehlgeschlagene Umwandlung (mit dem Grund und beiden Wegen zum neuen Versuch) und einen alten Ordner, der noch da
  ist.
- **Ein Deployment sagt dir, wenn ein alter Qdrant-Sidecar auf dem Board ist**: Pakete mit Docker-Images früherer
  Versionen starteten den Container `qdrant-sidecar`, den nichts mehr nutzt. Nach einem erfolgreichen Deployment
  (`llm-cli deploy run`, das Deployment-Fenster, das KI-Tool `deploy_to_device`) schaut das Framework mit `docker ps`
  (höchstens 20 Sekunden lang), ob er noch auf dem Board ist, und sagt dann, ob er läuft oder gestoppt ist und wie du
  ihn dort entfernst. Mojo Oracle zeigt für dieses Board eine Zeile aus dem letzten Deployment. Das Framework stoppt
  oder entfernt den Container nie.
- **Wohin fertige Modelle kommen: der Ausgabeordner und ein Speichern-Dialog** (achte Runde, Antwort 3): fertige
  Builds (Golden-Artefakte) werden im Ausgabeordner gespeichert (Einstellung `output_dir`, Standard `~/llmcf/output`;
  unter Windows in deinem Benutzerordner), den du einmal mit **File > Output Folder...** oder `llm-cli config
  output-folder PFAD` festlegst, zum Beispiel auf einen Ordner auf einer großen zweiten Platte; eine KI kann ihn nicht
  ändern. Ist ein Build fertig, den du in der GUI gestartet hast, öffnet sich für jedes Golden-Artefakt der
  Ordnerdialog des Systems im Ausgabeordner: OK speichert es dort oder in dem Ordner, den du wählst; Abbrechen
  speichert nichts woanders und löscht nichts, der Build bleibt im Build-Ordner des Frameworks (Einstellung
  `build_dir`), und **Speichern…** im Tab „Deployment“ oder `llm-cli build save` speichert ihn später. `llm-cli build
  start --output PFAD` (an einem Terminal ohne diese Angabe: eine Frage mit dem Ausgabeordner als Vorgabe, `-` lässt
  ihn im Build-Ordner); ohne Terminal, und für Ditto und MCP-Clients, kommen Builds in den Ausgabeordner. Speichern
  benennt auf derselben Platte um; auf eine andere Platte wird jede Datei kopiert, per sha256 geprüft und erst dann
  aus dem Build-Ordner entfernt (einem Link wird nie gefolgt; fehlt etwas in der Kopie, nennt das Ergebnis es, und
  der Build-Ordner behält seine Kopie); vorher wird der freie Platz geprüft, belegte Namen bekommen `-2`, und
  Systemordner (unter Windows auch die Programmordner, ProgramData, die Wiederherstellungs- und Startordner und der
  Papierkorb jeder Platte), der Programmordner, der Build-Ordner und der persönliche Ordner werden abgelehnt (als
  Ausgabeordner auch ein Ordner, der einen davon enthält, wie dein Home-Ordner). Der Tab „Deployment“,
  `deploy list-artifacts` und `list_artifacts` listen den Ausgabeordner, den Build-Ordner (frühere Builds bleiben
  dort), frühere Ausgabeordner, nachdem du ihn geändert hast, und die Ordner, in die du gespeichert hast;
  `start_build` sagt einer KI, wohin der Build kommt.
- **Gleiche Builds werden vor dem Bauen genannt** (achte Runde, N11): `check_build_request` (`earlier_builds`, mit
  einem Hinweis) und das Build-Formular der GUI melden einen fertigen Build mit zu 100 % denselben gewählten
  Optionen: das Modell und sein genauer Commit (ein lokales Modell über Name, Größe und Zeit jeder Gewichtsdatei),
  das Target und der sha256 seines Ordners, Board, Format, Quantisierung, Importance-Matrix und Kalibrierdaten,
  Optimierung, Kontextlänge, GPU-Nutzung und Aufgabe. Andere Gewichte oder eine andere Quantisierung sind nie gleich.
  Du nimmst ihn oder baust noch einmal (**Den vorhandenen Build nehmen** / **Noch einmal bauen** / **Abbrechen**);
  die Frage von `start_build` sagt, wenn sie einen gleichen Build noch einmal baut. Nichts wird von selbst
  übersprungen. Jeder Build hält seine Optionen in `build_info.json` fest (`build_options` mit ihrem sha256).
- **Platzprüfung vor jedem Wissens-Snapshot und ein Snapshot vor jedem Import eines Wissens-Packs** (achte Runde,
  Antwort 2, N13): bevor ein Snapshot angelegt wird, wird seine Größe geschätzt. Passt er neben der Reserve, die immer
  frei bleibt, nicht hin, wird kein Snapshot angelegt, und die Änderung läuft nicht (Deep Ingest, eine
  Wiederherstellung, ein Pack-Import, die Aufnahme der eigenen Dokumente des Frameworks); ein „trotzdem weiter“ gibt es
  nicht. KI-Clients bekommen vor der Frage den Blocker `snapshot_no_room` mit deiner Handlung; die GUI bietet
  **Knowledge Snapshots...** an, um Snapshots zu löschen oder zu exportieren. Passt er, lässt aber wenig Platz, eine
  Lagewarnung (`disk_tight`). Ein Import eines Wissens-Packs sichert die Wissensbasis jetzt zuerst (`pre_pack_<Zeit>`);
  ohne Platz wird nichts aufgenommen, und das Pack wird später aufgenommen. Ein Pack, das sich noch nicht aufnehmen
  lässt, wird beim nächsten Start ohne weiteren Snapshot noch einmal versucht, und ein belegter Name bekommt `-2`, so
  wird nie ein Snapshot überschrieben. Die Wissenszeile von Mojo Oracle (mit Ort, Grund und dem Hinweis, dass nur deine
  Liste Platz schafft) und `llm-cli knowledge snapshots` zeigen, was die Snapshots im Vergleich zum freien Platz
  belegen. Mojo prüft auch den freien Platz des Build-Ordners des Frameworks. Nichts wird von selbst gelöscht.
- **Löschen mit der vollen Liste, und der Papierkorb** (Delegationsentwurf 3.10, Runde 5 f; Runde 6 10.4): Jedes
  Löschen von etwas, das du sehen kannst, fragt zuerst im eigenen Fenster des Frameworks (der Lösch-Dialog der GUI oder
  eine Tabelle und eine Frage im Terminal) und nennt jedes Teil mit Namen, vollem Pfad oder Docker-ID, Dateinamen,
  Größe und was es ist; „Nein“ ist die Vorgabe. Wo du aus einer Liste wählst, ist nichts angehakt, die Liste wird aus
  genau dem Angehakten neu gebaut (ihr Hash wird als `deletion_sha256` auditiert), und die Schaltfläche nennt Anzahl und
  Größe. Löschen verschiebt Dateien und Ordner zunächst in den Papierkorb (`.trash` in ihrem Datenordner; ein
  Verschieben auf eine andere Platte wird abgelehnt, nie kopiert). Nichts verlässt den Papierkorb von selbst: Nach
  `trash_retention_days` (Vorgabe 14, von einer KI nicht änderbar) erinnert er nur. **File > Trash...** und
  `llm-cli trash list|restore|empty` stellen Einträge wieder her oder löschen die angehakten endgültig; kein Tool leert
  den Papierkorb, und keine KI erreicht die Papierkorb-Ordner. Die optionalen Tools `list_trash` und
  `restore_from_trash` (verborgen, bis du sie freigibst) zeigen einer KI den Papierkorb mit Namen und stellen nur
  wieder her, was sie selbst löschen wollte. Wissens-Snapshots löschst du mit `llm-cli knowledge snapshots
  prune|delete` oder unter **Tools > Knowledge Snapshots...** (Kästchen zum Anhaken), oder du exportierst sie mit
  `knowledge snapshots export NAME... --to ORDNER` (jede Kopie per sha256 geprüft; ein Export löscht nie). **Tools >
  Framework Build Images...** listet die Docker-Images des Frameworks zum Anhaken, wie `llm-cli doctor --fix --tier 2`;
  der Selbstheilungs-Dialog für eine volle Docker-Platte öffnet es. `llm-cli doctor` sagt, wo Docker seine Images ablegt
  und wie viel die Images des Frameworks belegen; diesen Ordner zu verlegen ist ein Schritt für Administratoren
  ([Container-Einrichtung](docs/Container_Setup_DE.md#10-wo-docker-images-ablegt)).
- **Der Faktenkatalog und die Einordnung je Tool** (Delegationsentwurf 4.1-4.6, `orchestrator/tools/
  facts_catalog.py`, Texte in `orchestrator/utils/l10n_facts.py`): Jeder Befund vor einer Rückfrage hat eine feste
  Art. Harte Hindernisse (volle Platte, Offline-Modus, ein nicht gekoppeltes Board oder ein geänderter Host-Schlüssel,
  ein Modell mit Bedingungen ohne Zugang, der nicht gelesene Hinweis zu Deep Ingest, ein zurückgezogenes Pack, ...)
  lehnen vor jeder Rückfrage ab, mit ihrem Grund-Code, einem nächsten Schritt für die KI und, getrennt davon, dem,
  was die Person tun kann; ein Hindernis, das eine KI beheben kann, nennt das Tool, das es behebt.
  Situationshinweise (knapper Speicherplatz, ein großer Download, knapper oder zu kleiner Arbeitsspeicher, viele
  Seiten, ein ungetestetes Modul, eine ersetzte Board-Laufzeit; nie die Zahl der Wissens-Snapshots, die keine Grenze
  haben) bringen eine Regel für eine KI mit; Sicherheits- und
  Rechtshinweise (Pickle-Gewichte, ein Basis-Image außerhalb der Docker Official Images oder nicht gepinnt,
  Hersteller-Downloads über der weichen Grenze, ein ungeprüftes oder Community-Target, der erste Build von KI-Build-
  Code, ein nicht vertrauenswürdiges Modell nativ auf einem Board oder in der Sandbox, die die Person nicht für KIs
  erlaubt hat
  (`bench_untrusted_sandbox_ok`), Signieren als die Person, Modelle mit Bedingungen, Lizenzen) bleiben immer bei der
  Person, ein Basis-Image außerhalb der Docker Official Images auch nach einem bestätigten Build damit, und Fakten,
  die sich nicht prüfen ließen, sind der Sicherheitshinweis `facts_unknown`, nie „kein Problem“ (die Person liest in
  klaren Worten, was nicht geprüft werden konnte, nie einen internen Code). Builds, Rezept-Builds, Downloads, Pack-Importe und -Exporte, Deep Ingest,
  Deployment-Pakete und Deployments, die Board-Tools, die lokale Laufzeit, Benchmarks, das Erstellen von Modulen und
  Einstellungen melden sie. Eine Entscheidung ist an den Entscheidungs-Digest der Fakten gebunden, der freien
  Speicher und Byte-Zahlen auslässt; ein Build oder Download unter einem Dauerauftrag (und später einer Vollmacht)
  nutzt nur den Commit, den sein Nachschlagen ermittelt hat (Hindernis `commit_required`, wenn Hugging Face nicht
  gefragt werden konnte). Die Sicht der KI auf die Fakten (`delegation.ai_view`) enthält nie einen Fakt, den nur die
  Person sieht. Die Codes der Hinweise sind feste Bezeichner, bereit für eine spätere Wahl „für diese Modellversion
  nicht mehr fragen“ (Entwurfsfrage Q3), die noch nicht gebaut ist.
- **Ein Smoke-Test mit einem echten Client** (`scripts/mcp_client_smoke.sh`, Prüfung `scripts/mcp_smoke_check.py`):
  lässt Claude Code (`claude -p`, vier vorgegebene Prompts) in einer Wegwerf-Umgebung gegen den stdio-Server des
  Frameworks laufen (Home, Hugging-Face-Home, XDG-Ordner, Benutzerordner, Zugangsdaten nur im Speicher, das
  Null-Keyring-Backend, kein Fenstersystem; der Server läuft unter `env -i` und bekommt so keine Variable des Aufrufers)
  und prüft anhand der isolierten Audit-Datenbank, nie anhand der Antwort des Modells: die Client-Zeile, `get_status`,
  ein unbekanntes Argument, das mit Vorschlag abgelehnt wird, ein erklärter Alias, der bei einem Aufruf, der lief, und
  bei einem abgelehnten festgehalten und dem Client in beiden gezeigt wird (gelesen aus den Tool-Ergebnissen der
  JSON-Ausgabe des Clients), und ein Aufruf, der eine Bestätigung braucht und ohne Antwort nicht läuft (eine Frage an
  die Person oder ein Ende mangels Antwort; eine Ablehnung vor jeder Frage, etwa eine verweigerte Berechtigung, zählt
  nicht). Er endet mit 77 (übersprungen), wenn der claude-Befehl fehlt oder nicht angemeldet ist, liest, kopiert oder
  übergibt nie Claude-Zugangsdaten oder Tokens, löscht seinen temporären Ordner (samt dem Protokoll des Clients über den
  Server und dessen temporären Dateien) und braucht kein Docker; eine manuelle oder optionale CI-Prüfung (CONTRIBUTING,
  „Der Smoke-Test mit einem echten Client“).
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
  (`confirmed_builds.json` im Benutzerordner, signiert wie die Berechtigungsdatei: eine Handänderung, ein anderer
  Schlüssel oder eine fehlende Signatur zählt als keine Bestätigung), und der Build-Dauerauftrag deckt die folgenden
  Builds ab. Ein per
  Digest gepinntes Basis-Image außerhalb der Docker Official Images zählt, sobald ein Build mit genau diesem Image
  bestätigt wurde; ein Tag nie. Die Regelversion des Build-Dauerauftrags ist jetzt 2, Daueraufträge nach der
  engeren Regel verfallen. Bis zu diesem ersten bestätigten Build trägt die Frage den Sicherheitshinweis „erster
  Build“ (die Person sieht von einer KI erstellten oder installierten Build-Code einmal selbst, Entwurfsfrage Q1),
  auch für ein Hardware-Modul, das eine KI ohne das Ja der Person erstellt hat (gebunden an das sha256 seiner
  Definition: unter demselben Namen neu erstellt, wird es erneut gezeigt).
- **Das Ende eines Deployments im Audit-Log**: Endet ein `deploy_to_device`-Job (erledigt, gescheitert oder
  abgebrochen), wird ein eigener Abschlusseintrag mit Job, Gerätenamen und Gerätezustand geschrieben, verknüpft mit dem
  `started`-Eintrag des Aufrufs.
- **Optionale Tools `delete_artifact` und `restore_knowledge_snapshot`** (MCP-Entwurf T10): ein gebautes Modell
  (Ordner und Golden-ZIP) per ID und sha256 löschen oder die Wissensbasis als Job durch einen früheren Snapshot
  ersetzen. Beide zerstören etwas: standardmäßig verboten und verborgen, bis du sie freigibst (die ganze Klasse
  „extern“ zu erlauben genügt nicht), jeder Aufruf mit Frage, nie ein Dauerauftrag. Über ein Löschen entscheidest nur
  du im eigenen Fenster des Frameworks (Dittos Dialog oder Eingabeaufforderung, bei einem MCP-Client eine wartende
  Freigabe), nie im Formular des Clients, nie unter einem Dauerauftrag oder einer Vollmacht; die Registry lehnt jedes
  löschende Tool ab, das sich auf anderem Weg bestätigen ließe. Dieses Fenster nennt jedes Teil (den Ordner und sein
  Golden-ZIP) mit sprechendem Namen, vollem Pfad, Dateinamen, Größe und was es ist; eine KI sieht nur Bezeichnungen und
  Größen, und Dittos Kommandozeile ohne Terminal gibt nur den Aufruf und seine Argumente aus. Ein Löschen wird abgelehnt
  außerhalb des Ausgabeordners und des Build-Ordners des Frameworks (ein Modell, das du in einen anderen Ordner
  gespeichert hast, entfernst du selbst), solange ein Build irgendeines Programms des Frameworks in den Ordner
  schreibt oder
  dafür wartet, nachdem sich der Build geändert hat und wenn der Ordner nicht der ist, den die Frage nannte. Eine
  Wiederherstellung sichert immer zuerst die aktuelle Wissensbasis (`pre_restore_<Zeit>`), auch außerhalb von MCP,
  und löscht keinen Snapshot; sie bleibt außerhalb jeder Dauerfreigabe. `delete_artifact` verschiebt das Modell und
  sein Golden-ZIP in den Papierkorb, wo du sie wiederherstellen kannst (siehe „Löschen mit der vollen Liste, und der
  Papierkorb“).
- **Der MCP-Server in einem Container, den du gebaut hast** (`llm-cli mcp serve --transport http --container`):
  bindet die Schnittstellen des Containers, und nur innerhalb eines Containers; benanntes Token, Host- und
  Origin-Prüfung bleiben gleich. Veröffentliche den Port nur auf dem Loopback des Hosts (`-p 127.0.0.1:8765:8765`).
- **Das Framework in einem Container, auf deinem eigenen Computer gebaut** (Runde 5 h): Kein Framework-Image wird
  veröffentlicht oder geladen. `scripts/build_container_image.sh` (`make build`) prüft Docker, BuildKit und den
  Klon, stellt genau die Dateien bereit, die das Repository ausliefert, und baut `Docker Setup/Dockerfile` mit einem
  lokalen Tag (`lcf-framework:local`; ein Name mit Registry wird abgelehnt); danach zeigt es, wie du es startest. Das
  Dockerfile führt dieselbe Installation aus wie auf dem Rechner selbst (`install.sh`, das mit
  `LCF_CONTAINER_BUILD=1` nur die Docker-Engine und die Gruppe `docker` weglässt und `runuser` nutzt, wo `sudo`
  fehlt), auf Debian 13 slim per Digest, für einen Benutzer ohne Root-Rechte mit deinen IDs, mit den Quellen als nur
  lesbarer Build-Einbindung (kein Geheimnis in einer Schicht) und allem Persönlichen im Volume `/data`
  (Einstellungen, Tresor, Audit-Log, Tokens, Modelle, Builds, Datensätze, Logs); standardmäßig CLI und MCP-Server
  ohne Oberfläche (mit der GLib-Bibliothek, die der Kern von Qt braucht und die die CLI importiert), die
  Desktop-Bibliotheken der GUI nur mit `--gui` (alles, was das xcb-Plattform-Plugin von Qt lädt); eine Gruppe mit
  deiner Gruppen-ID, die Debian schon hat, wird weiterverwendet. Der Build endet mit einer Prüfung, dass sich die
  Befehlszeile importieren lässt und jede Systembibliothek da ist, die Qt lädt, und bricht sonst mit dem Namen des
  Fehlenden ab. Ein Build aus dem Repository-Ordner selbst wird abgelehnt. Die Compose-Datei lädt das Image nie (`pull_policy: never`), und jedes `docker run`, das der Builder
  zeigt oder die CI ausführt, sagt `--pull never`; sie veröffentlicht keinen Port und entzieht alle Capabilities;
  KI-Clients erreichen den MCP-Server über stdio (`docker exec -i lcf-framework llm-cli mcp serve`, von einem
  anderen Computer per SSH zum Host). Targets, die du anlegst, und Packs, die du installierst, bleiben im
  Programmordner des Containers: `make down` stoppt nur, und `make up`, `make gui` und `make clean` ersetzen oder
  entfernen nie einen Container, der außerhalb des Volumes Dateien von dir enthält; sie listen jede mit
  vollständigem Pfad, Dateiname, Größe und was sie ist (`make kept-files`, `scripts/container_kept_files.sh`).
  `docker.registry` und `docker.tags` der Target-Dateien sind als nur lokale Image-Namen beschrieben. Anleitung:
  `docs/Container_Setup_DE.md`.
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
  sobald ihre Modelle heruntergeladen sind. Vor jeder Aufnahme, jeder Wiederherstellung und jedem Import eines
  Wissens-Packs sichert sie einen Snapshot in `knowledge_snapshots/` des persönlichen Ordners; passt ein Snapshot nicht
  auf die Platte, läuft die Änderung nicht (siehe „Platzprüfung vor jedem
  Wissens-Snapshot“). Es gibt keine Grenze: Sie behält so viele, wie die
  Platte fasst, löscht keinen von selbst, auf keinem Weg (Runde 5 f), und keine Anzahl bringt eine Erinnerung oder
  eine Warnung. `llm-cli knowledge snapshots` (in der GUI: Tools > Knowledge Snapshots...) listet jeden mit vollem
  Pfad, Namen, Größe und was er ist, damit du die, die du anhakst, löschst (zuerst in den Papierkorb) oder
  exportierst (woandershin kopierst, per sha256 geprüft).
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
- **Dittos lokaler Server direkt oder in Docker** (DECISIONS Runde 7 D): `llm-cli ditto runtime ensure [--mode
  auto|bare-metal|docker] [--force]` installiert ihn, wenn er fehlt, und tut nichts, wenn er da ist (in einer der
  beiden Arten). Ist nichts installiert, wählt er direkt (ohne den Aufwand von Docker), oder Docker, wenn direkt hier
  nicht geht, und sagt warum. Solange Docker nicht erreichbar ist, bleibt der Docker-Modus, wie du ihn gewählt hast;
  ist die aufgezeichnete Art weg, sagt der Befehl, dass die Art gewechselt hat. `ensure` entfernt unterwegs nichts
  (kein alter Build wird durch Aufräumen entfernt). Der Docker-Modus startet Dittos Image, auf deinem Rechner aus
  demselben festgelegten llama.cpp-Rezept gebaut, auf einem Docker Official Image als Basis, per Digest festgelegt
  (`orchestrator/runtime/build/ditto-runtime.Dockerfile`, zwei Stufen); es wird nie als fertiges Image geholt und nie
  hochgeladen, ein Image mit Dittos Kennzeichnung aus einer Registry wird nie benutzt, und es wird vor dem Gebrauch in
  Containern aus ihm selbst geprüft. Der Container hält die Startregeln jedes
  Servers ein (`server_policy.py`): Der Port wird nur auf 127.0.0.1 veröffentlicht, Modellordner und Schlüsseldatei
  des Starts werden schreibgeschützt eingebunden, er läuft als dein Benutzer ohne Capabilities auf einem
  schreibgeschützten Wurzeldateisystem, Docker entfernt ihn, wenn er stoppt, und die GPU wird durchgereicht, wo das
  geht (NVIDIA Container Toolkit, `/dev/dri`), mit einem Hinweis, wo nicht. `--force` wechselt die Art (braucht ein
  Terminal): Die Modelle werden weiter genutzt, nichts wird noch einmal heruntergeladen; der direkte Build wird ins
  Image umgepackt, oder der Build des Images wird zum direkten Build, nachdem er seine Prüfungen auf diesem Rechner
  bestanden hat; sonst wird neu gebaut und der Grund genannt. Direkt und Docker sind zwei Alternativen (W2): Bevor
  die neue Art neben der alten gebaut wird, wird ihr Platzbedarf geschätzt (was ihr Build beim Bauen braucht und was
  danach bleibt) und mit dem freien Platz jedes beteiligten Laufwerks verglichen (Dittos Laufzeitordner, Dockers
  Image-Speicher, der temporäre Ordner des Image-Builds). Passt sie nicht, wird nichts gebaut: Du siehst die Größen
  und wählst, selbst Platz zu schaffen und noch einmal zu wechseln, oder die alte Art zuerst zu löschen (ihre volle
  Liste, eine Frage, nur an einem Terminal oder in der GUI; Ditto ist nicht verfügbar, bis die neue Art funktioniert,
  die Modelle bleiben; scheitert die neue Art danach, erfährst du das und bekommst die alte Art wieder angeboten). Das
  Löschen zuerst wird nur angeboten, wenn die neue Art danach passt: Sie wird noch einmal geschätzt, ohne den Build
  der alten Art, der sich dann nicht mehr nutzen lässt, und mit dem Platz, den die Objekte der alten Art freigeben.
  Sobald die neue Art funktioniert, wird jedes Objekt der alten Art mit
  vollständigem Pfad oder Name, ID, Dateiname, Größe und was es ist aufgelistet: ihre Build-Ordner, Dittos Images und
  Container, ihre Einträge in `mode.json` und `active.json` und das Log der Image-Builds. Eine Frage löscht genau diese
  Liste endgültig, nicht in den Papierkorb („Nein“ ist vorgewählt, keine Auswahl je Objekt; ohne Terminal wird nichts
  entfernt). Die Installer bekommen einen Ditto-Schritt
  (`install.sh --ditto=auto|bare-metal|docker|skip`, dasselbe für `install.bat`, `LCF_DITTO` für unbeaufsichtigte
  Läufe), der als du läuft, nie als root, im Container-Build des Frameworks nichts tut, Größe und Lizenz eines Modells
  vor dem Download zeigt und die Installation nie scheitern lässt. Die KI-Einstellungen zeigen die Art und bieten den
  Wechsel über denselben Code-Pfad an. Ein KI-Client liest die Art in `get_status`; wechseln und entfernen darfst nur
  du. `runtime status` zeigt die Art.
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
  aktiv werden. Das Framework löscht keinen Build von selbst: Eine Installation behält die älteren Builds und sagt,
  wie viele (Dittos Builds und die Builds der Boards liegen in diesem einen Speicher). Ein Build von einem deiner
  anderen Rechner kann importiert werden: Seine Dateien werden gegen das Manifest geprüft, seine Build-ID gegen seinen Bauplan und sein Commit gegen den Pin, und du
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
  Benutzerordner, den sich mehrere Rechner teilen. `llm-cli ditto runtime prune` listet die alten
  Builds und die Installationen des vorgebauten Archivs einer anderen llama.cpp-Version, jeweils mit vollem Pfad und
  Größe, nichts angehakt, und löscht nur die, die du am Terminal auswählst und bestätigst (standardmäßig in den
  Papierkorb). Den aktiven Build, den CPU-Ersatz, den Build, aus dem ein laufender Server gestartet wurde, und die
  Builds der Boards listet es nie. `llm-cli device runtime prune GERÄT` tut dasselbe für die älteren Laufzeit-Pakete
  eines Boards auf deinem PC; ein neuer Build oder ein Deployment behält sie.
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
  `<meta name="robots">` / `<meta name="llm-cross-compiler-framework-bot">` (auch unter dem früheren Namen
  `llm-conversion-framework-bot`) mit `noindex`, `none`, `noai` oder `noimageai` markiert sind, folgt den Links von
  `nofollow`-Seiten nicht und beachtet das W3C TDM Reservation Protocol (`/.well-known/tdmrep.json` je Site, Header
  und Meta-Tag `tdm-reservation`, PDF-Metadaten).
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
  *Werkzeuge > Eingabeverlauf löschen...* vergisst ihn nach einer Frage, die jede Datei aufzählt, die gelöscht wird
  (den Verlauf und die Kopie eines beschädigten: vollständiger Pfad, Dateiname, Größe, was es ist; „Nein“ ist die
  Vorgabe), und löscht nur diese.
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
  Vorschlag ab, der sich nach der Rückfrage geändert hat, und lehnt die Freigabe eines manuellen Schritts ab, bevor
  etwas gefragt wird (auch einer vollen Docker-Platte, `docker_prune`: keine Selbstheilungs-Strategie löscht etwas).
  Dittos GUI-Dialog und CLI-Abfrage zeigen Rückfragen bis zu 16.000 Zeichen (ein scrollbarer Dialog, der Pager); ein
  MCP-Formular bleibt bei 4.000.
- **Grenzen je Client für Tool-Aufrufe** (Ditto und MCP-Clients): lesende Tools 20 Aufrufe in 10 Sekunden,
  `start_build` 3 unfertige Builds und 20 Starts am Tag, 30 Rückfragen pro Stunde und eine Build-Warteschlange von
  höchstens 20 unfertigen Anfragen je Framework-Prozess für KI-Aufrufer (die eigenen GUI- und CLI-Builds des
  Menschen werden immer eingereiht); nach einem Nein wird derselbe Aufruf 10 Minuten lang nicht erneut erfragt, und
  drei Nein zu einem Tool pausieren seine Rückfragen für 30 Minuten. Eine
  erreichte Grenze ist eine protokollierte Ablehnung `rate_limited`, die sagt, wann es wieder geht. Die
  Wiederholungssperre lässt einen abgelehnten Aufruf 10 Minuten nach seinem letzten Scheitern wieder laufen (bisher
  war er bis zum Ende des Prozesses gesperrt) und fasst die Aufrufe einer MCP-Verbindung über eine zufällige ID
  zusammen, nie über eine Speicheradresse, die eine spätere Verbindung wieder bekommen könnte; Ditto hört nur nach
  Fehlern ohne Fortschritt vorzeitig auf, Tools aufzurufen, mit bis zu 4 Schritten mehr, solange er einen
  Eingabefehler behebt (ein Aufruf an den falschen Computer ist einer); ein Aufruf, der auf eine Entscheidung
  wartet, zählt nie als Fehler.
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
  Aufrufers, `--link ditto` nutzt Dittos Berechtigungen per Verweis (für einen Client, der genau Dittos
  Berechtigungen haben soll).
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
  wartet die Frage im Framework auf dich (eine Antwort aus dem Formular des Clients zählt für einen solchen Aufruf
  nie, auch wenn du es gesetzt hast, während das Formular offen war): Ein Hinweis in der Statusleiste der GUI öffnet
  **Tools > Pending Approvals...**, und `llm-cli tools approvals` (`list`, `show`, `approve`, `decline`; braucht ein
  Terminal) funktioniert auf einem Rechner ohne Bildschirm. Der Client bekommt `waiting_for_approval` mit einer
  Job-ID; sobald du freigibst, führt das Framework den Aufruf selbst unter dem Aufrufer des Clients aus, außer ein
  Fakt, von dem die Entscheidung abhängt, hat sich inzwischen geändert (ein neuer Commit, ein neuer Hinweis wie ein
  jetzt knapper Speicherplatz: dann wirst du erneut gefragt; freier Speicher, Byte-Zahlen oder die Uhrzeit, nach der
  ein Sicherungs-Snapshot benannt wird, die sich nur bewegt haben, fragen nie erneut, der Aufruf läuft dann mit den
  Fakten von jetzt), und der Client liest das Ergebnis mit
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
  `external_text`). Keine Eingabe nennt Adresse, Benutzer, Port, Passwort, Fingerabdruck oder Pfad. Das Konto, mit
  dem sich das Framework anmeldet, und Pfade auf dem Gerät erreichen nie eine KI: `list_devices` sagt „device
  account“, die Anmeldung (Konto und Adresse) steht nur im eigenen Fenster des Frameworks (nie in einem Formular, das
  ein Client lesen kann, und nicht im sprechenden Namen eines Geräts), und Ausgabe, Fehler und Begründung des
  Deployments erreichen eine KI ohne den Namen des Kontos, seinen Home-Ordner und Gerätepfade, auch wenn das
  Deployment geklappt hat.
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
  nie aktiv, und ein abgebrochener Modell-Download behält seine `.part`-Datei; der Build einer KI entfernt keinen
  alten Build (alte Builds bleiben, bis du sie entfernst). Nur Dittos eigene Katalogmodelle werden nativ gemessen;
  ein für ein Board gebautes Modell nie (siebte Runde, 2'): `run_benchmark` misst es in der Docker-Sandbox, und seine
  echten Werte kommen vom Board (`run_device_benchmark`). Die Board-Tools erreichen nur vertraute Geräte mit dem
  vertrauten Host-Schlüssel,
  werden immer erfragt und nie von einer Daueranfrage abgedeckt; ihre Fakten, Folgen, Fortschritte, Prüfergebnisse und
  Fehler (auch bei Erfolg) nennen nie das Gerätekonto (weder die gekoppelte Anmeldung noch das Konto, das die
  Deploy-Einstellungen nennen) oder einen Pfad auf dem Board (das Fenster der Person zeigt die Anmeldung und den Pfad
  des Modells). Ist Dittos Server noch nicht gebaut oder das Modell einer Stufe noch nicht
  heruntergeladen, lautet die Ablehnung `missing_information` und nennt `build_local_runtime`, das die KI selbst
  aufrufen kann.
- **Benchmark-Läufe dieses Rechners** (`~/.llm-framework/benchmarks/`): `llama-bench`-Ergebnisse des selbst gebauten
  Builds (Prompt- und Erzeugungsgeschwindigkeit, höchster Speicherbedarf) mit dem sha256 des Modells und der Build-ID;
  `get_status` zeigt die neuesten.
- **Der Chat-Verlauf mit signierten Zeilen** (`chat_feed.sqlite` im Benutzerordner, Modus 0600; Delegations-Entwurf
  5.3): der eine Speicher für das, was das kommende Chat-Panel zeigt - deine Nachrichten, Dittos Antworten, Hinweise,
  wartende Fragen, Entscheidungen einer KI mit ihrer Begründung (das Audit-Log behält nur den Hash der Begründung),
  Ergebnisse von Arbeit, die eine KI gestartet hat. Jeder Prozess des Frameworks hängt in einer eigenen Transaktion an;
  jede Zeile ist mit einem vom Audit-Schlüssel abgeleiteten Schlüssel signiert (`sign_row`/`verify_row`), und eine
  Zeile, die geändert wurde, nicht signiert ist oder mit einem anderen Schlüssel geschrieben wurde, wird nie angezeigt
  (der Leser erfährt, wie viele übersprungen wurden; Zeilen einer neueren Framework-Version werden getrennt gezählt).
  Jeder Text wird vor dem Speichern und vor jedem Kürzen geschwärzt (ein Schlüssel, den eine Längengrenze teilt, lässt
  keinen Rest zurück), KI-Text vorher von unsichtbaren Zeichen bereinigt, eine Zeile hat kein Feld für die Ausgabe
  eines Tools oder rohe Argumente, Fakten nur für den Menschen kommen nie in die Fakten, die eine KI nutzen darf,
  Parameter sind einzeilig und als Framework- oder Fremdtext markiert, und von wem eine Zeile stammt, kommt aus der
  Aufrufer-Kennung (im Namen des Menschen schreiben nur die GUI und das Terminal). Ein Schreiben, von dem die
  Entscheidung einer KI abhängt, löst einen Fehler aus, wenn es nicht gelingt (auch bei voller Platte), jedes andere
  schlägt offen fehl, und Lesen wartet nie länger als seine kurze Zeitgrenze. Den Chat leeren kann nur der Mensch (kein
  Code, den eine KI erreicht, ruft es auf): dafür braucht es den angenommenen Plan (vollständiger Pfad, Dateiname,
  Größe, was es ist), das Leeren wird mit dem Hash des Plans (`deletion_sha256`) vor jedem Löschen protokolliert, der
  gelöschte Text wird überschrieben, und die Einträge darüber, was KIs getan haben, werden ausgeblendet statt gelöscht.
  Dittos bisheriger Chat wird einmal übernommen (seine Werkzeug-Hinweise und Hinweise als Fremdtext); ohne KI erklärt
  das Framework eine Entscheidung allein aus ihrem Eintrag, eine fremde Angabe in einer Zeile und in
  Anführungszeichen. Warnungszeilen nutzen das gemeinsame Vokabular der Warnungen (Codes, die Arten aus dem Katalog,
  geschlossene Zahlenwerte). Sprechende Namen (`Core/speaking_names.py`) zeigen genaue Framework-IDs und maskierte
  Werte in KI-Text bei der Anzeige mit Namen, nie in Code und nie bei einer bloßen Ähnlichkeit, und behalten die
  ursprünglichen Worte; `llm-cli doctor` prüft, dass sich der Verlauf schreiben lässt und seine neuesten Zeilen
  stimmen. Noch entfernt nichts Zeilen von selbst.
- **Der Chat-Bereich rechts im Hauptfenster** (Delegations-Entwurf 5.1-5.9): ein andockbarer Bereich (F9 oder
  **Ansicht > KI-Chat-Bereich**, Strg+Umschalt+C schreibt hinein, ein Klick auf Ditto oder die Schaltfläche **KI-Chat
  (n)** zeigt ihn; nach links verschiebbar, frei schwebend möglich) mit deinem Gespräch mit Ditto, Hinweisen und den
  Einträgen darüber, was KI-Apps getan haben, einmal pro Sekunde aus dem signierten Chat-Verlauf aller Prozesse des
  Frameworks gelesen, ohne je darauf zu warten. Wartende Fragen kommen aus den ausstehenden Freigaben und bleiben unter
  **Braucht dich** mit wer fragt, was und wie viel Zeit bleibt (neu gezählt, solange sie wartet); eine neue zeigt
  einen verborgenen Bereich, ohne den Tastaturfokus zu nehmen, und holt die Tastatur nie von der Frage weg, auf der du
  gerade bist (Einstellung `chat_panel_auto_show`, die keine KI ändern kann). Ein Filter (Alles, Braucht
  dich, Entscheidungen, Chat mit Ditto), Routine-Entscheidungen einer App in einer Zeile zusammengefasst, „Neu, seit
  du zuletzt geschaut hast“ mit einer aus den Einträgen gezählten Zusammenfassung, sprechende Namen in KI-Text mit
  „Ursprüngliche Worte zeigen“, Tastaturwege zwischen den Einträgen, Ansagen für Bildschirmleser (Fragen mit Vorrang)
  und englische/deutsche Texte (`orchestrator/utils/l10n_chat.py`; der Bereich, seine Einträge im Menü „Ansicht“
  und die Schaltfläche der Werkzeugleiste folgen einem Sprachwechsel, und deutsche Texte nennen die Menüs so, wie das
  Fenster sie zeigt). Wo der Bereich sitzt, ob er gezeigt wird, sein
  Filter und der neueste gesehene Eintrag stehen in `gui_state.json` im Benutzerordner (nie in QSettings); eine
  beschädigte Datei ergibt die Vorgaben.
- **Mojo: Der Gesundheitscheck schaut (Mojo Oracle) und repariert (Mojo Miracle)** (sechste Runde, Entscheidung 9;
  siebte Runde, Punkte 11', M' und T; [docs/Mojo_DE.md](docs/Mojo_DE.md)): `llmcf mojo` (Mojo Oracle, Standard;
  `--oracle`) schaut nur. Zu jeder Warnung und jedem Fehler sagt es, was es gefunden hat, wo (der volle Pfad; eine KI
  liest eine Bezeichnung), warum das wichtig ist, einen Hinweis und ob Mojo Miracle es reparieren kann, und ein Satz
  zählt jedes Problem danach, was sich tun lässt. Kann Mojo Miracle etwas tun, fragt es danach im Terminal „Run Mojo
  Miracle now? [y/N]“; bei Ja läuft Mojo Miracle, ohne dass der Befehl noch einmal getippt wird. `llmcf mojo
  --miracle` macht nur harmlose Reparaturen (der Zustandsordner auf deinen Benutzer eingeschränkt; Dittos Server auf
  einen intakten gespeicherten llama.cpp-Build umgestellt, wenn der aktive fehlt): jede ein Probelauf, an seinen
  sha256 gebunden, dessen Art sich aus seinen Schritten ergibt, nur so angewendet, wie Mojo Oracle ihn gezeigt hat
  (ein Problem, das sich seitdem geändert hat, wird nicht repariert), zuerst im Audit-Log festgehalten, als
  Vergleichen-und-Tauschen am Ordner angewendet, der geöffnet wird, ohne einem Link zu folgen, immer nur eine, und
  erneut geprüft. Danach meldet es jedes Problem: repariert, schon weg, noch da oder nicht repariert mit dem Grund
  (Löschen braucht deine Liste, braucht dich, braucht einen Administrator, nicht automatisch reparierbar,
  fehlgeschlagen), und ein Problem, das danach zum ersten Mal gefunden wird. Speicherplatz freigeben geht nur über
  die Liste der Build-Images des Frameworks im Terminal, nichts angehakt; ohne Terminal laufen nur die harmlosen
  Reparaturen, und nichts wird gelöscht. `--json` fragt nie und löscht nie.
- **Mojo Oracle meldet die vertrauten Boards und prüft Dittos Image** (achte Runde N6; W1 vom 2026-10-16; siebte
  Runde D): Ein normaler Lauf nimmt keinen Netzwerkkontakt zu einem Board auf. Er meldet, was aufgezeichnet ist: einen
  Host-Schlüssel in den `known_hosts` des Frameworks, der sich geändert hat oder fehlt, und das letzte Deployment, das
  letzte Laufzeit-Deployment, die letzte Prüfung, den letzten Benchmark oder die letzte Hardware-Prüfung, die ein
  Board nicht erreicht haben, mit dem Grund-Code (`device_host_down`, `device_port_closed`, `device_auth_failed`,
  `device_host_key_changed`) und dem nächsten Schritt. Jeder Kontakt des Frameworks mit einem Board zeichnet das auf;
  ein Kontakt, der das Board erreicht und sich anmeldet, beendet die Aufzeichnung. Für ein Board, auf das so ein Fakt
  zeigt, bietet es die Prüfung an: im Terminal „Check <board> now? [y/N]“, in `--json` und für eine KI den Befehl oder
  die Anfrage, die es prüft. `llmcf mojo --oracle --board NAME` (Bezeichnung, Geräte-ID oder Adresse) prüft ein Board
  sofort: eine Verbindung zu seinem SSH-Port, ohne Anmeldung, ohne Passwort und ohne den Namen des Gerätekontos. Im
  Docker-Modus prüft es, dass Dittos Image da ist, auf diesem Rechner gebaut wurde (nie gezogen), vom festgelegten
  Commit ist und das geprüfte ist (`runtime.docker`); es baut, zieht oder entfernt nie ein Image.
- **`llmcf`, der neue Name der Kommandozeile** (siebte Runde, Punkt 11): ein zweites Konsolenskript neben `llm-cli`,
  dasselbe Programm; `llm-cli` bleibt. Der Framework-Container hat beide Befehle.
- **Die MCP-Tools `run_mojo_oracle` und `run_mojo_miracle` und der Prompt `check_installation`** (siebte Runde, Punkt
  11'): `run_mojo_oracle` (nur lesen) liefert jeden Befund mit `where` als Bezeichnung (nie ein Host-Pfad), `why` und
  `repair` (`how`: `miracle`, `deletion_list`, `person`, `admin` oder `not_automatic`, mit dem nächsten Schritt).
  `run_mojo_miracle` repariert ein Problem, dessen `repair.how` `miracle` ist: Jeder Aufruf wird mit dem Plan gefragt
  (der volle Pfad nur im eigenen Fenster des Frameworks), keine dauerhafte Freigabe, keine Vollmacht, nie ein Löschen;
  jedes andere Problem wird vor der Frage mit seinem Grund abgelehnt, und das Ergebnis sagt, ob repariert wurde und,
  wenn nicht, warum. Die Vollmachtsgruppe für harmlose Reparaturen kommt mit den Vollmachten. `run_mojo_oracle` nimmt
  keinen Kontakt zu einem Board auf; der Befund eines Boards, auf das ein aufgezeichneter Fakt zeigt, nennt die Anfrage,
  die es prüft (`check_board`), und `{"board": "<Bezeichnung>"}` prüft dieses eine vertraute Board sofort. Eine KI
  nennt ein Board mit seiner Bezeichnung oder Geräte-ID, nie mit einer Adresse (ein Board ohne eigenen Namen, dessen
  Bezeichnung seine Adresse ist, mit seiner Geräte-ID).
- **Die eigenen Dokumente des Frameworks in der Wissensbasis** (ENTSCHEIDUNGEN Runde 8, N12): **Tools > Index the
  Framework's Documentation...**, `llm-cli knowledge index-docs [--with-code]` und das MCP-Tool `index_framework_docs`
  (jedes Mal gefragt, läuft als Job) nehmen die Dokumentation des Frameworks (README, CONTRIBUTING, den Ordner `docs`)
  und auf Wunsch seinen Code und seine Konfiguration (nur was es mitliefert: nie deine Einstellungen, eigene Targets
  oder installierte Pakete) als Art `docs` in die Wissensbasis auf, damit Ditto das
  Innenleben des Frameworks erklären kann. Das passiert nie von selbst (bisher gab es die Indexierung, aber nichts rief
  sie auf); vorher wird ein Snapshot angelegt (ohne Platz dafür auf der Platte wird nichts aufgenommen, auch beim
  ersten Snapshot, gemessen an der Wissensbasis selbst; ein Snapshot, der sich nicht schreiben lässt, hält es
  ebenfalls an; bei wenig übrigem Platz sagen es die Frage oder der Befehl), eine erneut aufgenommene Datei wird
  ersetzt, eine gelesene, aber nicht aufgenommene Datei wird mit dem Grund genannt, und ein Export als Wissenspaket
  lässt diese Texte weg.
- **Deployment auf ein Board über ein Netzwerk, das wegbricht** (achte Runde, N4 und N5): Du nennst das Board über
  seine Adresse oder seinen Hostnamen (`myboard.local`; `llm-cli deploy run --host`, `--ip` bleibt als alter Name).
  Bricht das Netzwerk während eines Deployments weg (ein Board nur mit WLAN), wartet das Deployment auf das Board
  (Vorgabe 300 s, `--wait`), zeigt mit Zeit und Grenze, dass es wartet, und lässt sich abbrechen („Abbrechen“ im
  Deployment-Fenster, Strg+C in der CLI, `cancel_job`); das Ergebnis sagt dann, ob `deploy.sh` schon gestartet war,
  auch wenn das Netzwerk genau beim Start wegbrach. Es meldet sich mit derselben Prüfung des Host-Schlüssels neu an
  und macht weiter: Der Upload setzt bei den angekommenen Bytes fort, sobald ihre sha256 passt, und `deploy.sh` läuft
  auf dem Board für sich und wird einmal gestartet, nie zweimal. Ergebnisse von `deploy_to_device` haben zusätzlich
  `reason_code`, `resumed` und `waited_s`; sein Fortschritt nennt das Board über deinen Namen dafür.
- **Warum ein Board nicht erreichbar ist** (N6): die Grund-Codes `device_host_down`, `device_port_closed`,
  `device_auth_failed` und `device_host_key_changed` (`orchestrator/runtime/device/reasons.py`) mit dem, was passiert
  ist, und dem nächsten Schritt für die Person auf Englisch und Deutsch, in GUI und CLI. Eine KI bekommt den reinen
  Code mit einem nächsten Schritt, ohne Adresse und ohne Konto; sie sind die Blocker der Board-Tools und ersetzen
  `device_unreachable` und `host_key_changed` des Faktenkatalogs.
- **KV-Cache des Servers auf einem Board** (N7): `f16`, `q8_0` oder `q4_0` (`-ctk`/`-ctv`, geprüfte Wörter der
  gemeinsamen Startregel und des Startskripts). Standardmäßig automatisch: `q8_0`, wenn neben dem Modell auf dem Board
  wenig Speicher frei bleibt (aus seiner Probe), sonst `f16`; du kannst immer einen Typ wählen, und `f16` schaltet ihn
  aus. Gewählt mit `llm-cli device settings --kv-cache` oder im Deployment-Fenster (eine gespeicherte Wahl, die das
  Fenster nicht zeigt, bleibt, außer du wählst dort); die systemd-Unit und der Startbefehl nehmen ihn aus den
  Deploy-Einstellungen des Boards, ein im Deployment-Fenster gemachtes Paket nennt ihn im Startbefehl seines
  `deploy.sh`, und die Frage von `deploy_device_runtime` nennt ihn.
- **„Getestet auf Board X“** (N3): Ein bestandener Benchmark auf dem Board (`llm-cli device runtime verify` oder
  `deploy` mit `--bench-model`, `run_device_benchmark` und der neue Teil „Benchmark auf einem Board“ im
  Deployment-Fenster) wird bei jedem Build dieses Modells im Ausgabeordner, im Build-Ordner des Frameworks und in den
  Ordnern, in die Builds gespeichert wurden, vermerkt (`device_tests.json`, gefunden über die sha256 des Modells), mit
  dem KV-Cache der Messung neben dem, auf den der Server des Boards eingestellt ist. Das Deployment-Fenster zeigt einen
  solchen Build als „getestet auf <Board>“ und trägt nach dem Deployment eines GGUF-Modells Board und Modell für diese
  Messung ein.
- **Die Hände der KI im Fenster: das GUI-Tool `gui`, für Ditto und für KI-Apps gleich** (`orchestrator/tools/tools_gui.py`,
  `orchestrator/tools/gui_surface.py`, `orchestrator/tools/gui_bridge.py`, `orchestrator/gui/form_surface.py`): Das
  Build-Formular, der Assistent für Target-Module (Seite für Seite) und der Dialog der KI-Einstellungen melden ihre
  Felder an, und eine KI liest, was du eingegeben hast, und füllt Felder sichtbar aus. Ein Tool mit einem Feld
  `action` (`read_form`, `fill_form`, `wizard_state`, `wizard_open`, `wizard_fill`, `wizard_next`, `wizard_back`,
  `wizard_docs`, `show_window`) ersetzt die fünf GUI-Tools der Entwicklungsversion, so listet ein MCP-Client
  genau 40 Tools, so viele, wie manche Clients höchstens nehmen. Ditto und jeder MCP-Client nutzen dasselbe Tool und
  denselben Code (`GuiCore`, im GUI-Prozess): Ditto direkt, der Server eines MCP-Clients über eine private Brücke zum
  offenen Fenster. Nur dieses Konto erreicht die Brücke: ein AF_UNIX-Socket im Ordner `gui_bridge` des Benutzerordners
  (0700; unter Windows eine Named Pipe mit zufälligem Namen), ein zufälliger Schlüssel in `gui_bridge/bridge.json`
  (0600), den beide Seiten nachweisen, bevor etwas gesendet wird, unter Linux das Konto der Gegenseite geprüft, und
  nur JSON-Nachrichten. Das erste Framework-Fenster bedient die KI-Apps; ein anderes offenes Fenster übernimmt, wenn
  es geschlossen wird. Ist kein Framework-Fenster offen, wird `gui` mit dem neuen Blocker `gui_not_open` abgelehnt
  („Das Framework-Fenster öffnen.“; Version 4 der Vollmacht-Regeln); es bleibt aufgeführt. Lesende Aktionen werden wie
  Lese-Tools entschieden, die anderen wie ein Tool, das etwas ändert (standardmäßig mit Frage); deine Einstellung für
  `gui` gilt je Aufrufer (Ditto, die lokalen stdio-Clients, jedes benannte Token), ein neues Token kann es nicht
  nutzen, und eine Frage, die ein Client nicht zeigen kann, wird eine wartende Freigabe. Geheimnisse sind nur
  „gesetzt“ oder „nicht gesetzt“ und werden nie ausgefüllt; lokale Dateien erreichen die KI als Labels, und kein
  Home-Ordner (auch nicht der eines anderen Kontos) erreicht sie mit Namen; ein Feld, das du gerade bearbeitest, oder
  ein Wert, den du gewählt hast, wird nie überschrieben; ein Ausfüllen, das über die Logik des Formulars deine Wahl
  ändern würde, wird ganz zurückgenommen. Jedes ausgefüllte Feld nennt, wer es ausgefüllt hat: Ditto in Lila, eine
  KI-App in einer anderen Farbe mit dem Namen ihres MCP-Tokens oder ihrem eigenen Namen in Anführungszeichen als ihre
  Angabe (eine App sieht nie aus wie Ditto); **KI-Änderungen rückgängig machen** nimmt alle KI-Änderungen zurück und
  steht im Audit-Log, mit dem, der ausgefüllt hatte. Nichts wird abgeschickt: Start Build, Finish im Assistenten und
  Save bleiben deine Klicks, und ein Modul, das eine KI ausgefüllt hat, wird als KI-erstellt markiert, sodass du
  seinen ersten Build einmal siehst. Die KI-Einstellungen lassen eine KI nie den Anbieter, die Adresse, die
  Datenschutzstufe oder die Ersatzkette ändern. Jeder Aufruf geht durch die Tool-Registry des Aufrufers selbst
  (Berechtigungen, Grenzen, Audit-Eintrag mit dem Aufrufer).
- **Die Board-Empfehlung aus der Hardware-Probe** (`orchestrator/runtime/board_recommendation.py`): Nach einer Probe
  empfiehlt der Builder das Board des passenden Targets mit seiner Quelle (der SoC der Probe oder der Board-Name der
  Probe in der Liste bekannter Boards des Targets) und wie sicher es ist. Nur ein genauer Treffer wird eingetragen;
  sonst werden die Kandidaten und der Grund gezeigt, und du wählst; bis du wählst, fragt **Start Build**, ob das Board
  im Formular deins ist. Ein Board, das du gewählt hast, wird weder durch eine spätere Probe noch durch eine KI
  ersetzt, und ein Target, das du gewählt hast (auch über ein Board, das du gewählt hast, oder das eines Rezepts),
  bleibt auch: Das Target der Probe wird dann mit einem Klick angeboten. **Ask Ditto for Config** füllt das
  Build-Formular genauso wie Dittos GUI-Tools (markiert, rückgängig zu machen, deine eigenen Werte bleiben; das
  Protokoll nennt jeden Wert, den es nicht eingetragen hat, und warum), und eine Modell- oder Datensatzdatei, die du
  im Dateidialog gewählt hast, zählt als deine Wahl. Das Nachschlagen des Board-Namens online öffnet deinen Browser
  erst, wenn du zustimmst. Passt kein Target, bietet der Builder **Target mit dem Assistenten erstellen** (beginnend
  mit dieser Probe) und **Ditto fragen** an (eine Frage im Chat, die du abschickst; ein Entwurf, den du dort getippt
  hast, bleibt).
- **Dokumentations-Links für Deep Ingest** (`orchestrator/Core/doc_sources.py`): Die `docs_*`-Links des
  Quellenkatalogs für den Hersteller der Probe und das SDK füllen das Deep-Ingest-Fenster des Assistenten; du prüfst
  sie und startest das Einlesen. Sagt Ditto, dass für ein Modul Informationen fehlen, bietet der Assistent Deep Ingest
  mit diesen Links an. KI-Clients lesen sie über die Ressourcen `lcf://knowledge/doc-sources` und
  `lcf://knowledge/doc-sources/{probe_id}`, nur lesend; der MCP-Prompt `add_board` verweist auf sie, wenn
  Informationen fehlen. Der Kopf des Katalogs und die Dokumente sagen jetzt, dass diese Links zur Laufzeit gelesen
  werden.
- **Build-Fortschritt und Live-Protokoll im Builder** (`orchestrator/gui/builder_parts.py`): ein Fortschrittsbalken mit
  dem Schritt und dem Prozentwert aus dem Build-Service (der eigene Schritt des laufenden Builds, siehe Behoben) und
  darunter das Protokoll des Builds live (Geheimnisse geschwärzt, volle Pfade, reiner Text, jede Zeile einmal, höchstens
  20.000 Zeilen in der Ansicht; eine KI, die das Protokoll liest, bekommt die Ordner als Label), mit Suchfeld und einem
  Filter für Info, Warnungen oder Fehler. Zeilen, die der Service nicht mehr liefert, werden vermerkt.
- **Ditto ist als experimentell gekennzeichnet** (achte Runde, Q-2; `orchestrator/utils/ditto_notice.py`): Ditto
  bleibt optional. Der Chat-Bereich und die KI-Einstellungen zeigen ein Abzeichen **Experimentell**, und ein Hinweis
  sagt einmal je Benutzer, auf Deutsch oder Englisch: „Nutzung auf eigene Gefahr und Verantwortung. Der Ersteller von
  LLMCF haftet nicht.“ Er wiederholt den Gewährleistungsausschluss aus `LICENSE.txt` und fügt ihm nichts hinzu. In der
  GUI bleibt er oben im Chat-Bereich, bis du auf **Verstanden** klickst, und blockiert nichts; ein ausgeblendeter
  Chat-Bereich zeigt sich dafür einmal (ohne den Fokus), weil der Builder und der Modul-Assistent Ditto ohne den
  Chat-Bereich nutzen. `llm-cli ditto` und `llm-cli module generate-ai` geben ihn an einem Terminal einmal auf stderr
  aus (ohne Terminal wird nichts ausgegeben oder gespeichert). README und Features sagen dasselbe.
- **Lizenz und Urheberrecht jeder gecrawlten Seite** (achte Runde, Q-1 und Q-1'; `orchestrator/rag/licences.py`):
  Deep Ingest hält je Seite fest, bevor die Fußzeile entfernt wird, einen Lizenz-Link (`rel="license"`, den
  HTTP-Header `Link`), Lizenz-Metadaten (`<meta name="license">`, `dc.rights`, `dcterms.license`, schema.org
  `license`; in einem PDF seine XMP-Metadaten), einen Creative-Commons-Link in der Fußzeile oder in einem Satz, der
  die Seite lizenziert, und einen ausdrücklichen Urheberrechtshinweis; bei Code von GitHub die Lizenzdatei des
  Repositorys, wenn derselbe Crawl sie gelesen hat (sonst hat der Code keine bekannte Lizenz; sie gilt nie für Issues,
  Pull Requests, Diskussionen, das Wiki oder Releases). Nur was die eigene Lizenz der Seite nennt, zählt: ihre
  Adresse, SPDX-ID, ihr eigener Titel mit Version oder ihr üblicher Erlaubnistext, nie eine nebenbei erwähnte Lizenz,
  ein Bildnachweis, ein Link im Text eines PDFs oder eine andere Version; ein bloßer Link wiegt nie „All rights
  reserved“ auf. `llm-cli knowledge sources` listet jede Quelle mit ihrer Lizenz, ihrem Hinweis
  und dem, was ein Wissenspaket von ihr enthielte, und warum; `list_knowledge_sources` gibt KI-Clients die Lizenz-ID,
  ihren Zustand und die Art der Weitergabe nur in festen Wörtern (der Hinweis ist Text der Website und erreicht nie
  eine KI).

### Geändert
- **`llm-cli build start --format` bietet, was die installierten Targets bauen** (DECISIONS Runde 23 Punkt 7): statt
  jedes Formats der Engine (`tensorrt`, `coreml`, ..., die kein Target baut) nimmt `--format` ein Format, das das
  Target baut, wie seine Capabilities in `target.yml` es angeben, die Liste, die die Formatauswahl des Builder-Tabs
  anbietet (`build_formats.offered_formats`, eine Liste für beide; ein Target ohne Capabilities nimmt wie bisher jedes
  Format außer `hf`, ungeprüft). `--help` und die Shell-Vervollständigung nennen, was jedes installierte Target baut,
  gelesen aus den Targets, wenn sie gezeigt werden; ein Format, das das Target nicht baut, stoppt mit Exit-Code 2 in
  klaren Worten, die nennen, was jedes installierte Target baut (`build_formats.format_problem`). Groß- und
  Kleinschreibung des Formats spielt keine Rolle mehr.
- **„Den vorhandenen Build nehmen“ kopiert ihn in den gewählten Ordner, und `llm-cli build start` fragt auch**
  (DECISIONS Runde 23 Punkt 9): `build start` prüft vor allem anderen auf einen fertigen Build mit zu 100 % denselben
  Optionen, wie der Builder-Tab (`BuildService.earlier_builds`, der Commit einmal nachgeschlagen, wenn er gleich sein
  könnte), und stellt am Terminal dieselbe Frage (nehmen, noch einmal bauen, abbrechen; ohne Terminal baut er wie
  angegeben). „Nehmen“ kopiert den früheren Build jetzt samt Golden-ZIP in den Ordner, in den der Build gegangen wäre
  (`--output`, die Antwort auf die Frage oder der Ausgabeordner; in der GUI der Ordnerdialog, der am Ausgabeordner
  öffnet), wo er vorher nur genannt wurde: `artifact_output.plan_copy` und `copy_artifact`, die eine Plattenregel vor
  der Kopie, jede Kopie per sha256 geprüft, der frühere Build bleibt, wo er ist. Der gleiche Build, der dort schon
  liegt, wird nicht noch einmal kopiert; ein anderer Build unter diesem Namen wird erst ersetzt, wenn du die
  vollständige Liste dessen angenommen hast, was ersetzt wird („Nein“ ist die Vorgabe; in den Papierkorb oder
  endgültig; vorher ins Audit-Log geschrieben).
- **Die Docker-Tabelle im Deployment-Tab entscheidet, was das Paket enthält** (DECISIONS Runde 23 Punkt 6): Die
  Tabelle in **Use Docker Containerization?** zeigt keine Attrappen-Auswahl mehr, die nichts bewirkte (Llama.cpp
  (CPU), RKLLM (NPU), TensorRT (GPU)); sie listet die Container, die ein Paket mit Docker-Images für das gewählte Profil
  enthält, aus der einen Liste, die `llm-cli deploy package --docker` verpackt
  (`deployment_manager.package_containers`): die Inference-Engine mit ihrem Image (Einstellung
  `image_inference_runtime`) und dem Hardware-Zugriff, den das Target des Profils ihr auf dem Board gibt. Jeder
  Container ist angehakt, das verpackt genau das, was `--docker` verpackt; ein Container ohne Haken bleibt aus dem
  Paket (`package_docker_config(docker, containers)`, über `make_package` und den einen Deploy-Schritt
  `person_steps.deploy_build`), und ohne Haken sagen **Deploy to Target** und **Create package only...**, warum, und
  tun nichts.
- **Wissen gehört immer zu einem Thema oder einem Target** (DECISIONS Runde 23 Punkte 1 und 8): Ein installiertes
  Wissenspaket liegt in `community/knowledge/<Thema oder Target>/<Paket>/` (früher installierte Pakete bleiben, wo sie
  sind), und jeder Auszug, den es bringt, trägt sein Thema oder Target. Ein Wissenspaket nennt sein Thema oder Target
  im Manifest: `llm-cli zoo export knowledge` braucht `--topic` oder `--target` (ein Target dieses Computers), ebenso
  das Export-Fenster (**Gehört zu:**) und das Werkzeug `export_pack`; ein Paket von vorher wird weiter geprüft und
  installiert, mit seiner ID als Thema, was die Zusammenfassung sagt und nur deine Bestätigung annimmt (keine
  dauerhafte Freigabe deckt das ab). `search_knowledge` und `list_knowledge_sources` nehmen `topic`,
  `llm-cli knowledge sources --topic` und das neue Feld und die Spalte **Thema oder Target** in **Tools >
  Wissensquellen...** grenzen die Liste ein. Eine lose Wissensdatei wird gelistet (`llm-cli knowledge loose`, Mojo,
  **Lose Wissensdateien einsortieren...** im Community Hub) und über die Prüfungen eines installierten Pakets in einen
  Themen- oder Target-Ordner einsortiert (`llm-cli knowledge sort NAME --topic|--target`), dann importiert. Der
  Wissens-Snapshot eines Target-Pakets (`knowledge/initial_knowledge.json`) wird wie ein Wissenspaket geprüft und unter
  diesem Target in die Wissensbasis des Empfängers importiert, mit einem Snapshot vorher; die README, die der
  Modul-Assistent daneben schreibt, sagt das. Der vorläufige Wissens-Export des Modul-Assistenten landet in einem
  privaten Arbeitsordner statt in `community/knowledge/`.
- **Ein Deploy-Schritt für einen Build und ein Ablauf für die Runtime eines Boards, für CLI, GUI und KI**
  (Konsolidierungskette c3-deploy-flow). `llmcf deploy run` und Deploy sowie Vorhandenes Paket deployen... im
  Deployment-Tab laufen jetzt über einen Kernschritt, `person_steps.deploy_build`: das Paket, das Deployment, dann
  der vertraute Eintrag des Boards. Der Tab baut sein Paket jetzt so wie `llmcf deploy package` und Nur Paket
  erstellen... (`make_package`): Ein Docker-Paket trägt den Namen des Inferenz-Images, und Abbrechen stoppt auch das
  Packen zwischen seinen Schritten (es bleibt kein Paket liegen). Der Grund, warum ein Paket nicht gebaut werden kann,
  kommt mit dem Ergebnis zurück statt über das geteilte `DeploymentManager.last_package_error`, das kein Code des
  Frameworks mehr liest (`create_deployment_package` behält es für ältere Aufrufer). Der vertraute Eintrag des Tabs
  wird so protokolliert wie in der CLI: Ohne das Audit-Log der Registry schreibt er seine Zeile jetzt ins Audit-Log
  im Benutzerordner, vorher schrieb er keine. Die Frage der GUI zu einem neuen Host-Schlüssel (vor dem Deployment des
  Tabs und im Hardware-Scan) schreibt die Zeile des Vertrauens genauso, wie es `llmcf deploy run` und
  `llmcf scan --device` tun. `llmcf device runtime deploy`, Laufzeit installieren... im Boards-Fenster
  und `deploy_device_runtime` der KI laufen über einen Ablauf, `steps.deploy_runtime_flow`. Er meldet sich an,
  schreibt den vertrauten Eintrag des Boards (nur für die Person), liest das Board und lehnt root vor jeder Frage ab,
  fragt zum Benchmark-Modell mit der Zustimmung (nur für die Person), installiert, schließt die Sitzung und
  vermerkt „getestet auf“. Jeder Aufrufer übergibt seine Anmeldung und seine Worte als Hooks. Die Hooks der KI
  enthalten keinen vertrauten Eintrag, keine Frage und keine frühe Ablehnung von root, ihre Ergebnisse bleiben also
  gleich; ein Test prüft das. Was die CLI ausgibt und die Fenster zeigen, bleibt sonst gleich.
- **Der Hugging-Face-Browser der GUI lädt in den Ordner `hf/` des Modellordners, geprüft, wie ein Download einer KI**
  (Entscheidung A6 des Maintainers; Konsolidierungskette hf). Neuer Ort: `<Modellordner>/hf/<Owner>--<Name>/<die
  ersten 12 Zeichen des Commits>/<Datei>`, derselbe Ordner wie bei `download_hf_file` einer KI (der Builder legt
  ganze Modelle weiter in `cache/models/hf/` ab); bisher speicherte der Browser über huggingface_hub in dessen Cache (`<Modellordner>/models--<Owner>--
  <Name>/`, Links auf Blobs, die nach ihrer Prüfsumme heißen). Ein Codepfad: `ModelManager.download_file` ruft jetzt
  `orchestrator/Core/hf_hub.py` `download_for_person` auf, das zuerst den Commit nachschlägt, genau die Datei dieses
  Commits mit dem Fetcher des Frameworks lädt (`HubFetcher`: fortsetzbare `.part`-Datei, Größe und sha256 des Hubs
  werden geprüft, bevor die Datei ihren Namen bekommt, das Token nur an huggingface.co, nie ein stilles `HF_TOKEN`)
  und zuerst die eine Speicherplatz-Regel anwendet (die Reserve von `Core/disk_plan` und was laufende Builds und
  Downloads reserviert haben; eine Ablehnung in der Sprache der Person) und während des Downloads eine eigene
  Reservierung im Ledger des Benutzerordners hält, sodass ein Build, der inzwischen startet, ihren Platz mitzählt. Es
  gilt das Token, das die Person im Browser eingibt, sonst das im Framework gespeicherte (`hf_token`), sonst keins. Das
  Fenster zeigt den Fortschritt und hat einen Anhalten-Knopf (der bisher geladene Teil bleibt; ein neuer Download setzt
  ihn fort). Modelle im alten Cache bleiben für KI-Aufrufer aufgeführt und für Builds nutzbar. Der neue Knopf **Alte
  Downloads übernehmen…** (`orchestrator/Core/hf_takeover.py`, freiwillig und einmalig) kopiert sie in den neuen
  Ordner, jede Datei gegen die Prüfsumme geprüft, mit der sie geladen wurde (der Name des Blobs: die LFS-sha256 oder
  die Git-Blob-ID; bei einem Cache ohne Links, was der Hub für diesen Commit führt), unter der Speicherplatz-Regel mit
  eigener Reservierung; eine Datei, die nicht passt, oder eine andere Datei gleichen Namens im neuen Ordner bleibt, wo
  sie ist, mit ihrem Grund. Danach bietet er das Entfernen der alten Kopien nur im Löschdialog mit der genauen Liste
  an (der Cache-Ordner jedes Modells, dessen Dateien alle übernommen sind, und die Dateien des gemeinsamen Speichers
  des Caches, die nur sie nutzen, mit vollem Pfad und Größe; ein Ordner mit einer Datei, die die Übernahme nicht
  aufführen kann, wird nie angeboten; Papierkorb oder endgültig; „Nein“ ist die Vorgabe), zuerst im Audit-Log
  vermerkt; ohne dieses
  Ja wird nichts gelöscht. Jeder Schritt läuft in einem Worker-Thread. Die Texte stehen in
  `orchestrator/utils/l10n_hf.py` (Englisch und Deutsch). Im Offline-Modus lehnt der Browser vor jeder Anfrage ab
  (siehe Behoben).
- **Ein Build sind wieder die vier Stufenmodule, und `build.sh` führt sie nur aus** (Entscheidung U9 des
  Maintainers; Konsolidierungskette build4). `targets/Rockchip/modules/build.sh` ist ein Stage-Runner über
  `source_module.sh` (die Quellen der Werkzeuge, aus denen das Image gebaut wurde), `config_module.sh`
  (vereinheitlicht den Auftrag, lehnt ab, was das Target nicht bauen kann, und schreibt den Build-Plan),
  `convert_module.sh` (das Modell in voller Genauigkeit, die Importance-Matrix, RKLLM und RKNN über ihre Format-Helfer
  `rkllm_module.sh` und `rknn_module.sh`) und `target_module.sh` (Quantisierung, `build_info.json`); die Arbeit des
  Verteilers liegt in der Stufe, zu der sie gehört, und ein GGUF-, RKLLM- oder RKNN-Build erzeugt dieselben Dateien
  mit denselben Namen wie bisher (`tests/test_build_stages.py` hält Befehle, Ausgaben, `SHA256SUMS`, die gelesenen
  Umgebungsvariablen und die Exit-Codes fest, vorher und nachher). Neu: Nach jeder Stufe, die wirklich fertig ist,
  eine Marke zum Fortsetzen im Arbeitsordner des Builds (`stages/<Auftrag>/`) mit einer Prüfsumme der Eingaben der
  Stufe (Auftrag, Revision und Dateien des Modells, Quantisierung, Target, Format, Kalibrierdaten, der llama.cpp-Pin
  des Images und der SHA-256 jedes Moduls und Skripts) und den Dateien, die sie geschrieben hat; erneut auf demselben
  Arbeitsordner mit denselben Eingaben gestartet, werden fertige Stufen übersprungen, und eine geänderte Eingabe
  oder eine fehlende Datei führt diese Stufe und jede spätere erneut aus (`build.sh --fresh` übergeht die Marken,
  `--to STUFE` hält nach einer Stufe an). Jede Stufe schreibt eine Fortschrittszeile (`=== Stage 3/4: convert ===`,
  `... done in 41 s`, `... resumed`, `... failed (exit 1)`), und Build-Status, Builder und `llm-cli build status`
  zeigen die Stufe („Running modules: convert (3/4)“) mit ihrem Anteil am Fortschrittsbalken. Die Engine übergibt den
  Hugging-Face-Commit des Modells als `MODEL_REVISION`. Ein optionales `modules/rotate_module.sh` läuft zwischen
  convert und target, der Platz für eine Rotation wie SpinQuant (ein Algorithmus wird nicht mitgeliefert).
  Benchmarks bleiben auf dem Board: Keine Stufe führt `benchmark_module.sh` aus, das das in seinem Kopf sagt und auf
  `llm-cli device runtime verify --bench-model` verweist. Die Funktionen der Module aus 1.x sind wieder erreichbar
  (mit nachgebauten Werkzeugketten getestet): Ein Hardware-Profil des Boards (`build.sh --hardware-config DATEI`) ergibt die CMake-Toolchain und
  das Board-Paket mit cross-kompiliertem `llama-cli` und `llama-server`, und `source_module.sh --clone-llama-cpp`
  (auch der Entrypoint-Modus `source --clone-llama-cpp`), von Hand gestartet, klont llama.cpp bei einem angegebenen
  Commit; vier versteckte Fehler dieser 1.x-Wege sind dabei
  behoben (eine nicht definierte Cross-Compile-Funktion, CMake-Flags als ein gequotetes Wort übergeben, eine
  Profilprüfung, die das Skript unter `set -e` beendete, Schreibzugriffe in den schreibgeschützten Cache-Ordner). Was
  sie taten und ein Build nicht mehr braucht (llama.cpp-Werkzeuge im Build bauen: Das Image hat sie; der alte
  Rückfall auf `convert.py`: Das gepinnte llama.cpp hat diese Datei nicht), läuft nicht wieder. Jedes Modul läuft
  auch allein mit seinen Optionen aus 1.x, wie im Entwurf des Maintainers: `convert_module.sh --input ORDNER --output
  DATEI --model-name NAME` schreibt das F16-GGUF nach DATEI, `target_module.sh --input DATEI.gguf --quantization TYP
  --model-name NAME` quantisiert dieses GGUF, ohne es erneut zu konvertieren, und behält es (Vorgaben aus 1.x: Q4_K_M
  und der Name der Datei), und `config_module.sh --hardware-config DATEI` schreibt Toolchain und Plan ohne Modell; ein
  Pfad als Option wird absolut gemacht. Die Stufe config liest die Datei, die die Hardware-Probe auf dem Board
  schreibt (ihre Schlüssel `ARCH` und `CPU_MODEL`; die Namen aus 1.x, `ARCHITECTURE_FULL` und `CPU_MODEL_NAME`, gehen
  weiter vor): Eine echte Probe-Datei bekam die allgemeinen ARMv8-A-Flags und eine für x86_64 die
  aarch64-Cross-Compiler, weil die Stufe nur die Namen aus 1.x las. Jetzt bekommt ein Cortex-A55, A72 (RK3576) oder
  A76 seine CPU-Flags, ein x86_64-Profil seine GGML-Flags für AVX2 und AVX512, und ein System, für das das Image keinen
  Compiler hat (ein 32-Bit-System auf einem Board schreibt `ARCH=armv7`), wird abgelehnt, bevor etwas geplant ist,
  statt Programme zu bekommen, die dort nicht laufen. Die Prüfsumme zum Fortsetzen umfasst auch den Ausgabeordner
  und das Hardware-Profil, das die Stufe config liest, wenn keins genannt ist, und eine verspätete Stufenzeile eines fertigen
  Containers nimmt dem Build seinen nächsten Schritt nicht mehr weg.
- **Die Stufe convert erkennt, prüft, zählt und testet, was sie umwandelt, und hinterlässt nichts Halbfertiges**
  (der Entwurf des Maintainers für das convert-Modul von 1.x, Nachtrag zur Entscheidung U9; Konsolidierungskette
  how2-convert). `scripts/model_check.py` (das Rockchip-Target und die Vorlage bringen dieselbe Datei mit; sie braucht
  nur das Python des Images und seine Standardbibliothek) erkennt die Eingabe: einen Hugging-Face-Ordner mit
  SafeTensors- oder PyTorch-Gewichten, eine GGUF-Datei (nichts wird umgewandelt, die Stufe target quantisiert sie),
  ein ONNX-Modell, eine einzelne Gewichtsdatei oder einen Ordner mit GGUF-Dateien. Was das Format des Builds nicht
  verwenden kann, wird mit dem Grund abgelehnt, bevor etwas Langsames läuft: Ein ONNX-Modell für GGUF („llama.cpp has
  no converter from ONNX to GGUF“) oder RKLLM nennt das Format rknn für die NPU (auf dem RK3566 führt die NPU Sprach-
  und Bildmodelle aus, das LLM läuft als GGUF auf der CPU), und eine einzelne Gewichtsdatei fragt nach dem Ordner des
  Modells. Vorher erreichte eine solche Eingabe den Konverter und scheiterte dort mit einem Traceback. Geprüft wird
  die Integrität der Eingabe (ihre JSON-Dateien, jeder SafeTensors-Header gegen die Dateigröße, wie die
  safetensors-Bibliothek ihn prüft, die Teildateien, die ein Index nennt, PyTorch-Archive, ein GGUF-Header; nur die
  Gewichte, die der Konverter liest, also ist eine übrig gebliebene `pytorch_model.bin.index.json` neben den
  SafeTensors eines bereitgestellten Repositorys kein Grund zur Ablehnung) und die
  jedes GGUF, das der Konverter geschrieben hat (die Daten jedes Tensors liegen in der Datei, wie der Lader von
  llama.cpp es prüft); die Warnungen von 1.x zu fehlender Konfiguration, fehlendem Tokenizer oder fehlenden Gewichten
  bleiben. Ein kurzer Funktionstest führt das geschriebene GGUF mit `llama-completion` aus, dem Einmal-Generator, der
  in 1.x `llama-cli` war (`llama-cli` selbst, wenn ein Image nur das hat): 8 Tokens, höchstens 120 s, offline. Ein
  Lauf, der mit einem Fehler endet oder abstürzt (llama.cpp bricht bei einem Tensor ab, den es nicht verwenden kann),
  lässt die Stufe mit den letzten Zeilen des Werkzeugs scheitern; eine Zeitüberschreitung oder ein von außen
  angehaltener Lauf (der Out-of-Memory-Killer) ist ein Hinweis; übersprungen wird er mit einem Hinweis, wenn das
  Image keines der beiden Werkzeuge hat, bei einem Embedding-Modell, bei den NPU-Formaten und im Auftrag `imatrix`.
  Die Statistik (Größe der Eingabe und der Ausgabe, Verhältnis, Dauer der Umwandlung, das Ergebnis des Tests; die
  Eingabe zählt die Gewichte, die der Konverter liest, keine zweite Kopie wie Mistrals `consolidated.safetensors`) steht
  im Log und als `conversion` in `build_info.json` (Zahlen und Wörter, keine Pfade), das die Build-Engine beim
  Vervollständigen des Manifests behält. Eine Datei, die ein Konverter ersetzt (die `--output`-Datei von 1.x, ein
  früheres Ergebnis), bleibt als `DATEI.convert-backup` erhalten, bis die neue ihre Prüfungen bestanden hat; eine
  gescheiterte oder angehaltene Stufe entfernt, was sie geschrieben hat, und legt die Sicherungen zurück, nach einem
  abgebrochenen Lauf tut es der nächste Start der Stufe (eine frühere Datei, die du von Hand zurückgelegt hast,
  bleibt). Ein zweiter Build in denselben Ausgabeordner findet das RKLLM- oder RKNN-Modell, das der Format-Helfer
  über die gleichnamige Datei des ersten geschrieben hat. Die Module bleiben mit ihren Optionen von 1.x einzeln
  aufrufbar. Die Charakterisierungstests vergleichen `build_info.json` ohne den neuen Schlüssel, und ihre
  nachgebauten Werkzeugketten schreiben GGUF- und SafeTensors-Dateien mit gültigen Headern (`tests/model_files.py`).
- **Neue Targets bekommen funktionierende Stufenmodule, und die Entrypoint-Modi führen den Stage-Runner aus.** Das
  `build.sh` der Vorlage ist derselbe Runner wie der von Rockchip (ein Test hält sie gleich), und ihre vier
  Stufenmodule nehmen die Werte des Wizards auf: Die Einrichtungsbefehle laufen einmal in der Stufe source (was sie
  exportieren, erreicht die späteren Stufen, auch beim Fortsetzen), die Quantisierungslogik ist das `case` der Stufe
  convert, die Packbefehle laufen in der Stufe target, und beide dürfen weiter `$QUANT` neben `$QUANT_TYPE` nutzen,
  wie beim Verteiler. Der Modulgenerator schreibt die convert- und target-Module der
  Vorlage statt „Placeholder“-Skripten, die mit Exit-Code 0 endeten; eine Stufe ohne Vorlage beendet den Build mit
  dem Grund. `entrypoint.sh pipeline EINGABE NAME [QUANTISIERUNG] [PROFIL]` und die Modi `source`, `config`,
  `convert` und `target` führen `build.sh` aus (Letztere bis zu dieser Stufe, mit den Moduloptionen aus 1.x). Die
  Blöcke `modules:` beider `target.yml`, der Wizard, das Tool `create_target_module`, Dittos Modul-Prompt,
  `llm-cli module create` (dessen nächste Schritte die Module nicht mehr Altlasten nennen) und die Leitfäden nennen
  die Stufe, zu der jeder Teil gehört; der Roadmap-Punkt, die Vier-Module-Skripte zu entfernen, ist anders erledigt
  (sie sind der Build).
- **Die Stufe config prüft ihren Plan, und die Stufe target macht wieder das einsatzfertige Paket der Version 1.x**
  (Nachtrag zu U9, DECISIONS Runde 16; Rockchip und die Vorlage). Mit dem Hardware-Profil eines Boards sucht die Stufe
  config die geplanten Compiler im Image (fehlt einer, wird nichts geplant), baut und linkt ein kleines C- und
  C++-Programm mit den geplanten Flags (und führt sie aus, wenn sie für diesen Rechner sind), prüft am ELF-Kopf, dass
  sie für das System des Boards sind, lässt `cmake -P` die erzeugte Toolchain-Datei lesen und prüft den Plan
  `build_config.sh` in einer sauberen Shell, bevor sie ihn behält. Die Build-Jobs werden auf die Kerne des Boards
  begrenzt (`CPU_CORES` oder `CPU_CORES_LOGICAL` der Probe-Datei), und ein 32-Bit-ARM-System wird mit dem schlichten
  Grund abgelehnt (die Boards haben 64-Bit-CPUs, das Image nur die aarch64-Cross-Compiler; installiere das
  64-Bit-System und lass die Probe erneut laufen), auch `ARCH=armv8l` einer Probe-Datei der Version 1.x (ein
  32-Bit-System auf einer 64-Bit-CPU); die Vorlage lehnt jedes Profil ab, das nicht vom System des Moduls ist, auch
  eines von einem System, das sie nicht kennt. Mit Profil macht die Stufe target eines GGUF-Builds das Paket in
  `output/packages/`: das Modell, `llama-cli` und `llama-server` für das Board, die Toolchain-Datei und das Profil,
  eine Model Card, `test_model.sh` (prüft die Dateien und dass der Rechner die Programme des Pakets ausführt, ein
  32-Bit-Arm-Paket auch auf einem 64-Bit-Arm-Kernel, dann eine kurze Antwort des Modells), `deploy.sh`
  (`USER@HOST [ZIEL]` über `ssh` und `scp` mit `StrictHostKeyChecking=yes`, oder auf dem Board selbst nach
  `/opt/llm`), `MANIFEST.json` mit jedem Detail des Builds und `SHA256SUMS`; das Modell läuft einmal auf diesem
  Rechner, die Programme müssen für das Board sein, und jede Datei muss zu ihrer Prüfsumme passen, bevor das Paket
  bleibt. Dieser Lauf ist der Funktionstest der Stufe convert (`scripts/model_check.py test`, eine Implementierung
  seit dem Merge beider Ketten): Ein Lauf, der mit einem Fehler endet oder am Modell abstürzt, stoppt das Paket; eine
  Zeitüberschreitung, ein Stopp von außen (der Out-of-Memory-Killer), ein Werkzeug, das die CPU dieses Rechners nicht
  ausführen kann oder das im Image nicht startet, und ein Embedding-Modell sind Hinweise, die das Manifest nennt, wie
  in der Stufe convert (vorher hatte das Paket einen eigenen Test, der sie ablehnte, sodass ein Build seine Stufe
  convert bestehen und dann sein Paket aus demselben Grund nicht bekommen konnte, und ein Embedding-Modell bekam kein
  Paket). Ihr Cross-Build schaltet jede Option von llama.cpp ab, die etwas herunterladen kann, wie jeder
  llama.cpp-Build des Frameworks (`DOWNLOAD_OPTIONS_OFF` von `orchestrator/runtime/build/recipe.py`): Am Pin ist
  `LLAMA_USE_PREBUILT_UI` standardmäßig an und holte in einem Container mit Netz die Web-Oberfläche von
  `llama-server` aus einem Hugging-Face-Bucket. `build.sh --package` (auch ein von Hand gestartetes `target_module.sh` und die Entrypoint-Modi `target` und
  `pipeline`) macht es auch ohne Profil, mit den schlichten Vorgaben des Targets, die das Manifest nennt; das eigene
  Deployment des Frameworks bleibt `llm-cli deploy` über seine SSH-Sitzung. Jede Quantisierungsmethode des gepinnten
  `llama-quantize` ist aufgeführt und wird angenommen (neu: `Q1_0`, `Q2_0` und die Kurznamen `Q3_K`, `Q4_K`,
  `Q5_K`), und `IQ2_M`, `IQ3_XXS`, `IQ3_XS` und `Q2_K_S`, die `llama-quantize` ohne Importance-Matrix ablehnt,
  werden vor dem Konvertieren abgelehnt, wenn IMatrix aus ist. `source_module.sh --clone-llama-cpp` klont den Pin des Frameworks,
  wenn kein `LLAMA_CPP_COMMIT` angegeben ist, und prüft den Checkout; seine `LLAMA_CPP_PIN_TAG` und
  `LLAMA_CPP_PIN_COMMIT` sind Pin-Stellen, die `tests/test_build_recipe.py` prüft. Die Überschreibung aus
  `config repos` für llama.cpp (`LLAMA_CPP_REPO_OVERRIDE`) wirkt jetzt: Das Image holt den gepinnten Commit daraus
  (Build-Argument `LLAMA_CPP_REPO`), wenn sie eine https-Adresse ohne Zugangsdaten ist, und bricht weiterhin ab, wenn
  es nicht genau diesen Commit bekommt; die Doku sagte, sie habe keine Wirkung.
- **Das Board-Paket hält die Build-Regeln des Frameworks ein: die Platzregel, keine Downloads, die CPU des PCs, für den
  es ist** (Entscheidungen des Maintainers zur Konsolidierung Stufe C, Welle 3; Kette build-safety; Rockchip und die
  Vorlage). Das Paket kopiert das Modell neben das des Builds, und Läufe von Hand und im Pipeline-Modus packen immer;
  jetzt prüft die Stufe target zuerst die eine Platzregel (`scripts/board_package.py room`: Die Platte von
  `output/packages/` muss fassen, was das Paket kopiert, und 2 GiB oder 2 % von ihr frei lassen, die Reserve von
  `orchestrator/Core/disk_plan.py`), für das Modell vor dem Funktionstest und dem Cross-Build und für jede kopierte
  Datei direkt vor dem Kopieren, und stoppt mit den Worten der Regel ("Not enough disk space for the package for the
  board: output disk: 1.2 GB needed, 2.5 GB free, 2.1 GB must stay free. Free disk space ...") und ohne Paket. Das Image
  hat kein Framework, deshalb bringt das Skript die Zahlen und Worte der Regel mit (ein Test vergleicht sie); die
  Reservierungen des Benutzerordners sieht ein Build-Container nicht. Die CMake-Flags eines Moduls können keinen
  Download von llama.cpp mehr einschalten: Der Modul-Generator lehnt solche `cmake_flags` ab (Wizard, KI-Vorschläge,
  MCP, CLI; `-DLLAMA_USE_PREBUILT_UI=ON`, `-DLLAMA_CURL=ON`, `-DGGML_CUDA_CCCL_VERSION=...`,
  `-DFETCHCONTENT_FULLY_DISCONNECTED=OFF`, `-DLLAMA_SUBPROCESS=ON` und ähnliche), mit dem Grund des Build-Rezepts
  (`recipe.refused_option`, die Regel, die `check_download_options` auf jeden Plan anwendet), die Ordnerprüfung
  erzeugter Module, Exporte und Community-Pakete lehnt sie in jeder Skriptzeile ab, die nicht die der Vorlage ist (in
  jeder Schreibweise, die die Shell cmake übergibt: `-D NAME=WERT`, `-DNAME:TYP=WERT`, in Anführungszeichen), und der
  Cross-Build des Pakets übergibt die Optionen des Frameworks nach den Flags des Moduls, damit sie immer gelten. Was
  keine spätere Option abschaltet, stoppt das Paket vor allem Langsamen, mit dem Grund und ohne Paket: ein Flag des
  Moduls, das eine Option aus `NEVER_SET` des Rezepts setzt (sie lädt herunter, sobald sie gesetzt ist), oder CMake ein
  Skript ausführen lässt (`-C`, ein Initial-Cache, oder `-P`, das jede Option setzen kann; der Generator lehnt diese
  auch in `cmake_flags` ab). Ein Wert in Anführungszeichen, der einen Download ausgeschaltet lässt
  (`-DLLAMA_BUILD_TESTS="OFF"`), wird nicht mehr abgelehnt. Sein `llama-server` wird jetzt ohne Unterprozesse gebaut
  (`LLAMA_SUBPROCESS=OFF`, Entscheidung D22), wie jeder Server des Frameworks; vorher hatte der Server des Pakets sie.
  Ein x86_64-Hardware-Profil wird nur dann mit `-march=native` geplant, wenn es das dieses Build-Rechners selbst ist
  (dasselbe CPU-Modell und dieselben CPU-Flags wie `/proc/cpuinfo` hier); ein Profil eines anderen PCs bekommt die
  CPU-Merkmale seiner Probe, so wie das Build-Rezept einen Build für einen anderen Rechner plant (alle 14 x86-Optionen
  von GGML ausdrücklich, ihre Compiler-Flags auf `-march=x86-64`, ein Merkmal, das die Probe nicht bestätigt hat,
  weggelassen, und bei einer Probe-Datei aus 1.x die Merkmale, die ihre CPU-Flags nennen). Vorher bekam jedes
  x86_64-Profil `-march=native`, und so gab ein Build-Rechner mit AVX-512 einem PC mit nur AVX2 Programme, die er nicht
  ausführen kann.
- **Der Code ist in Schichten geordnet, und ein Test hält sie ein** (Konsolidierung; Entscheidung des Maintainers vom
  2026-10-17: ein Repository, Schichten per Test durchgesetzt). `tests/test_layering.py` ordnet jedes Modul von
  `orchestrator/` einer Schicht zu (unten, remote, Kern, Fachlogik, Tool-Definitionen, Adapter), liest jeden Import
  (auch in Funktionen, unter `TYPE_CHECKING` und verzögerte) und schlägt fehl bei einem Import, der nach oben zeigt,
  bei einem Adapter, der einen anderen importiert, bei PySide6, click/rich, mcp/uvicorn oder paramiko außerhalb ihres
  Zuhauses und bei einem größeren Import-Zyklus; die heutigen Ausnahmen stehen mit ihrem Grund in
  `tests/data/layering_allowlist.json`, die nur schrumpfen darf. Gemeinsame Helfer sind aus den Tool-Modulen in die
  Schicht gezogen, die sie braucht, und die alten Namen bleiben: Pfad-Labels für KIs nach
  `orchestrator/security/paths.py` (mit `scrub_for_ai`, der einen Status-Bereinigung KI-seitiger Texte, und den
  Home-Ordner-Mustern, die das GUI-Werkzeug, der Paket-Export und die Datenschutz-Masken teilen), die privaten
  atomaren Schreiber nach `orchestrator/utils/atomic.py`, die einmaligen Hinweise nach
  `orchestrator/utils/notice_store.py`, Docker-Image-Referenzen nach `orchestrator/utils/image_ref.py`, der
  SSRF-sichere HTTP-Adapter nach `orchestrator/security/http.py`, der Audit-Schlüssel nach `Core/vault_key.audit_key`,
  die Basis-Images eines Pakets nach `Core/community_pack.py`, die Snapshot-Prüfung vor einem Index nach
  `Core/knowledge_snapshots.py` und das Audit eines gespeicherten Moduls nach `Core/module_generator.py`. Für die
  Person ändert sich nichts; die Fachlogik importiert dafür keine Tool-Definitionen mehr, und der größte
  Import-Zyklus schrumpfte von 81 auf 9 Module.
- **Ein nur lesender Host-Schlüssel-Speicher und die Vertrauens-Schreiber der Person**
  (Konsolidierungskette c2-remote-trust): `orchestrator/remote/trust.py` (`TrustStore`: `status`, `status_of`,
  `check` mit einem `KeyCheck`, `read_presented_key`; die Host-Schlüssel-Fehler sind dorthin umgezogen) nutzen die
  neun Kopien von „Schlüssel lesen und prüfen“, jede behält nur ihre Regel (die Person darf vertrauen, eine KI wird
  abgelehnt); `orchestrator/remote/pairing.py` enthält die Schreiber der Person, und die drei Abläufe für Vertrauen
  bei der ersten Nutzung (`llm-cli deploy run`, `llm-cli scan --device`, die Host-Schlüssel-Frage der GUI) sind eine
  Entscheidung mit einer Frage je Kanal, "Nein" als Vorgabe wie bisher. Ein Schlüssel, dem du jetzt vertraust, kommt
  im selben Schritt ins Audit-Log (`host_key_trusted`), auch wenn die Anmeldung danach scheitert; vorher wurde nur
  der Eintrag des Boards nach einer erfolgreichen Anmeldung festgehalten. Der Deployment-Tab bindet das Deployment
  an den Schlüssel, den du in seiner Frage bestätigt hast, und trägt das Board damit ein, sodass ein Deployment aus
  der GUI vier statt fünf Verbindungen zum SSH-Port des Boards öffnet. Die alten Namen in
  `orchestrator/utils/ssh_trust.py` funktionieren weiter.
- **Der Code, der mit einem Board spricht, bekommt sein eigenes Paket, `orchestrator/remote/`**
  (Konsolidierungskette c2-remote-base; für die Person und eine KI ändert sich nichts). Die vorhandenen
  Umsetzungen sind dorthin umgezogen, statt eine zweite Kopie zu bekommen: die Host-Prüfung der Remote-Probe, die
  Speicher- und die known_hosts-Schreibweise und das Lesen einer gespeicherten Adresse (`address.py`), die Grundcodes
  aus `orchestrator/runtime/device/reasons.py` mit dem Code, den jeder Aufrufer festhält (`reasons.py`,
  `reach_code`; der alte Pfad ist dasselbe Modul), das einzeilige Ende einer Board-Antwort, die behaltenen
  Ausgabezeilen des Deployments und die drei Kopien des Texts eines laufenden Deployments für den Deployment-Tab,
  `llm-cli deploy run` und das Deploy-Tool (`text.py`). Die Kopien der Benutzerprüfung (fünf), der Host-Prüfung
  (drei), des Ersatzcodes (sechs) und des Kontakt-Eintrags für Mojo (sechs, jetzt `record_reach`) rufen die eine
  Umsetzung auf; die Fehler der Board-Schritte teilen sich `RemoteFailure`. paramiko wird erst bei der ersten Nutzung
  importiert, deshalb laden `orchestrator/utils/ssh_trust.py` und alles, was es importiert, keine SSH-Bibliothek, und
  das Deployment und der Builder-Tab fragen mit einem echten Import, ob SSH geht (eine kaputte Installation galt dort
  als vorhanden und bei der Anmeldung als fehlend).
- **Eine Board-Sitzung für jeden Kontakt mit einem Board, `orchestrator/remote/session.py`** (Konsolidierungskette
  c2-remote-session, Befund 2 des Architektur-Audits). Was nur die Board-Sitzung des Deployments konnte (sich erneut
  anmelden und dabei den Host-Schlüssel wie bei der ersten Anmeldung prüfen, auf ein Board warten, dessen Netz
  abgebrochen ist, einen Upload bei dem fortsetzen, was angekommen ist, eine Installation verfolgen, die auf dem Board
  selbständig läuft), ist jetzt `RemoteSession`, über eine `Transport`-Schnittstelle
  (`orchestrator/remote/transport.py` `ParamikoTransport`), sodass das Laufzeit-Deployment, die Board-Prüfung und der
  Benchmark auf dem Board als Nächstes dieselben Schritte nutzen können (Kette c2-remote-callers). Das Deployment
  behält sein Paket und den Job von deploy.sh und ist ein dünner Adapter: seine Ergebnisse, Gründe und
  Gerätezustände sind dieselben, festgehalten von `tests/test_remote_session.py` an der Sitzung vor dem Umzug. Seine
  Zugangsdaten haben einen Typ: `KeyOnly()` (jeder KI-Weg) erreicht die Schlüssel-Installation nie, nur das
  `OneTimePassword` der Person kann koppeln. Jede Anmeldung liest und prüft den Host-Schlüssel des Boards über den
  einen nur lesenden Trust-Store (`orchestrator/remote/trust.py` `TrustStore.check`, mit dem Fingerabdruck, den die
  Person gesehen hat), wie jede andere Prüfung eines Host-Schlüssels. `RemoteShell` und die Board-Prüfung lesen die
  Antwort eines Boards mit
  dem einen begrenzten Leser des Transports, `ssh_trust.connect` und `paramiko_quiet` delegieren an den Transport,
  und die Grundcodes lesen die Fehlertypen von paramiko über ihn (paramiko hat in der Remote-Schicht jetzt nur noch
  den Transport als Zuhause). Jeder Befehl des Deployments hat jetzt das Zeitlimit und die Ausgabegrenze, die die
  Befehle der Geräte-Laufzeit hatten (120 s Stille, 1 MiB; eine sha256-Prüfung auf dem Board wartet mindestens 900 s
  und bei einem großen Paket länger), und die Sitzung hält die Verbindung alle 15 s am Leben: Ein Board, das mitten in
  einem Befehl nicht mehr antwortet, hält das Deployment nicht mehr fest. So ein Befehl zählt als Netzabbruch (die
  Verbindung von paramiko steht dann noch, bis TCP aufgibt): Das Deployment wartet wie nach jedem Abbruch auf das
  Board, meldet sich mit erneut geprüftem Host-Schlüssel wieder an und setzt den Upload fort oder verfolgt die
  Installation weiter, höchstens 20-mal, statt mit „could not be followed“ zu enden.
- **Der OpenSSH-Rückfallweg des Deployments ist ein zweiter Transport, kein zweites Deployment** (Entscheidung A5,
  Konsolidierungskette c2-remote-system, Befund 3 des Architektur-Audits). Wo paramiko nicht importiert werden kann,
  läuft `deploy_package` jetzt über dieselbe Board-Sitzung wie mit paramiko, über den OpenSSH-Client dieses Computers
  (`orchestrator/remote/transport.py` `SystemSshTransport`): Das Deployment wartet auf ein Board, dessen Netzwerk
  weggebrochen ist (seine Wartezeit, die Warte-Ereignisse, Abbrechen), setzt den Upload bei den Bytes fort, deren
  sha256 auf dem Board passt, startet `deploy.sh` einmal selbständig auf dem Board (abgekoppelt, hinter einer
  Markierung) und verfolgt es nach einem Abbruch weiter, statt eines Uploads mit `scp` und einer an die SSH-Sitzung
  gebundenen Installation, die ein Abbruch beendete. `deploy run` und der Deployment-Tab lesen den Host-Schlüssel des
  Boards vor dem Deployment weiter mit paramiko, ohne paramiko halten sie also wie bisher dort an. Jeder
  OpenSSH-Aufruf kommt aus einem Bauer (`openssh_argv`, `openssh_options`; `ssh_trust.system_ssh_options` ist sein
  alter Name): nur der Schlüssel und die known_hosts des Frameworks, nie eine Frage und nie ein neuer Schlüssel, wie
  bisher, jetzt auch ein Zeitlimit für die Anmeldung (`ConnectTimeout`) und Keepalive (`ServerAliveInterval`,
  `ServerAliveCountMax`), jeder Befehl als ein `sh -c` mit Zeitlimit (das auch gilt, während die Eingabe des Befehls
  geschrieben wird) und Ausgabegrenze, und `UserKnownHostsFile` in Anführungszeichen: Ein Zustandsordner mit einem
  Leerzeichen im Pfad (ein Windows-Kontoname wie „Jane Doe“) teilte die known_hosts des Frameworks in zwei Dateien,
  die es nicht gibt, sodass der Schlüssel jedes Boards als unbekannt galt. `GlobalKnownHostsFile` ist das Null-Gerät
  (`/dev/null`, unter Windows `NUL`): OpenSSH liest nicht mehr zusätzlich die systemweite known_hosts dieses
  Rechners, ein Schlüssel, den ein anderes Programm oder ein Administrator dort eingetragen hat, lässt also nie ein
  Board herein, das du im Framework nicht bestätigt hast (`StrictHostKeyChecking=yes` bleibt). Ein Passwort geht nicht
  mehr stillschweigend verloren: OpenSSH fragt nie danach, also meldet sich das Deployment mit dem Schlüssel des
  Frameworks an, und sein Ergebnis sagt, dass das Passwort nicht genutzt wurde. Ein Deployment, das an einen dir gezeigten
  Fingerabdruck gebunden ist, wird ohne paramiko weiter abgelehnt, ebenso eine Board-Sitzung, die auch prüfen muss,
  welches Board antwortet (seine Geräte-ID). Der SSH-Schlüssel des Frameworks wird beim Anlegen
  und bevor OpenSSH ihn nutzt nur für dein Konto lesbar gemacht
  (`orchestrator/utils/atomic.py` `owner_only`: Gruppen- und andere Bits weg unter Linux und macOS, unter Windows eine
  geschützte ACL nur für dein Konto, die OpenSSH für Windows bei einem privaten Schlüssel verlangt). Eine
  Schlüsseldatei, die OpenSSH trotzdem ablehnt (andere können sie lesen, oder sie ist beschädigt), wird als Problem auf
  diesem Computer gemeldet (`local_problem`, nie beim Board vermerkt), nicht als Ablehnung der Anmeldung durch das
  Board, obwohl OpenSSH dann ohne den Schlüssel weitermacht und mit dem „Permission denied“ des Boards endet. Auch
  gegen einen echten `sshd` getestet (`tests/test_remote_system_ssh.py`).
- **Das Laufzeit-Deployment, die Prüfungen auf dem Board, der Benchmark auf dem Board und die Board-Prüfung laufen
  über die eine Board-Sitzung** (Konsolidierungskette c2-remote-callers). Ein WLAN-Abbruch beendet sie nicht mehr mit
  „the SSH connection failed“: `runtime/device/remote.py` `RemoteShell` ist eine Fassade über `RemoteSession`, also
  wartet die Sitzung auf das Board (N5), meldet sich mit erneut geprüftem Host-Schlüssel wieder an und macht weiter.
  Das Laufzeit-Archiv geht über SFTP in seinen privaten Staging-Ordner und geht bei dem weiter, was angekommen ist (ein
  Board ohne SFTP bekommt es wie bisher über die Standardeingabe eines Befehls); die Prüfungen lesen nur und laufen
  erneut; ein Schritt, der nicht zweimal laufen darf, endet mit einem klaren Satz, sobald das Board wieder antwortet,
  und der Staging-Ordner wird trotzdem entfernt; ein Board, das nicht wieder antwortet, beendet jeden späteren Schritt
  sofort mit seinem Grundcode. Der kurze llama-bench-Lauf startet auf dem Board für sich (`run_detached`) und wird nach
  einem Abbruch wieder verfolgt, nie zweimal gestartet (Runde 16 G4: Arbeit läuft weiter, während niemand zusieht; ein
  Board ohne nohup führt ihn wie bisher angehängt aus); `RemoteSession.follow` verfolgt einen schon gestarteten Job.
  Sein privater Ordner (`<Installationsordner>/.bench.XXXXXXXX`) wird danach entfernt, auch nach einem Fehler oder
  einem Strg+C im Terminal; einer, den ein Abbrechen im Fenster Boards oder ein `cancel_job` einer KI hinterließ (das
  Abbrechen schließt die Verbindung, das Board ist dann nicht mehr erreichbar), geht mit dem nächsten Benchmark auf
  dem Board, sobald kein Lauf seinen Job mehr verfolgen kann: In ihm hat sich länger als das Zeitlimit des Benchmarks
  nichts geändert (`sweep_bench_folders`, so wie der lokale Benchmark die Sandbox-Ordner eines Absturzes entfernt).
  Die Board-Prüfung sendet ihre nur lesenden Befehle nach einem Abbruch erneut. Der KI-Weg
  (`runtime/device/service.py`) öffnet eine `KeyOnly`-Sitzung (Entscheidung 2), mit denselben Ablehnungen und Texten
  wie bisher; die Board-Schritte der Person (`person_steps.connect_stored`, `device runtime deploy|verify|prune
  --on-device` der CLI, das Fenster Boards) öffnen eine mit dem Einmal-Passwort der Person, wenn eines eingegeben ist,
  und `scan --device` übergibt seine Anmeldung an eine Sitzung. CLI und Fenster Boards sagen, wenn das Netz des Boards
  abbricht und wenn es wieder antwortet (EN/DE), jedes Warten neu (auch nach einem Upload, der von vorn begann),
  der Job einer KI zeigt es mit dem Label des Boards (auch der Job von `probe_remote_device`, der gar kein Warten
  zeigte: das Tool gibt die Ereignisse der Sitzung jetzt weiter wie die Deploy- und Benchmark-Tools). Ebenso
  **Board per SSH scannen...** im Tab Builder in seinem
  Protokoll, mit dem Knopf **Scan abbrechen**, solange der Scan läuft, wie ihn das Fenster Boards hat: Er beendet ein
  Warten auf das Board sofort (nicht erst nach 300 s), und nichts wird gespeichert (`scan_steps.scan_device` nimmt den
  `CancelToken` und `on_event` des Aufrufers). Die Wartezeile der CLI bei `device runtime deploy|verify|prune
  --on-device` und bei `scan --device` (das unter seinem Spinner stumm wartete) nennt die Taste, die das Warten beendet,
  " (Ctrl+C)", wie die von `deploy run`; ein Strg+C während des
  Wartens im Laufzeit-Deployment beendet den Befehl jetzt als Strg+C (das Aufräumen des Staging-Ordners, das das Board
  dann nicht erreicht, ersetzt es nicht mehr durch „the connection to the device is closed“), und ein Board, das nicht
  wieder antwortet, beendet diese Befehle (auch `scan --device`) mit dem verständlichen Grund und seinem nächsten
  Schritt (`_print_reason`,
  wie `deploy run` es ausgibt), nicht mit einem Satz, der nur den Grundcode nennt. Ein
  `CancelToken` ersetzt die vier Kopien von „den Client halten, damit Abbrechen ihn schließt“, und das Schließen einer
  Sitzung beendet ihr Warten. Die Board-Schritte der Person, `scan --device` und Mojo fragen
  `remote.transport.available()`, ob SSH geht, und die Meldungen der GUI für ein Problem auf diesem Computer
  (local_problem) nennen jetzt auch, was dort fehlschlug (die Worte des Systems und den Namen der Datei, nie ihren
  Ordner oder ein Konto). Wie bisher bleibt (Review der Kette): Ein Befehl, der stumm bleibt, während seine
  Verbindung noch antwortet (`exit 0` darauf), ist auf dem Board beschäftigt, nicht abgeschnitten; seine Zeitgrenze
  beendet ihn also sofort mit „no answer from the device within N s“, ohne zweite Anmeldung, ohne zweiten Lauf auf dem
  Board und ohne Zeile, dass das Netz abbrach. Das Archiv wird unter umask 077 angelegt, bevor der SFTP-Upload
  hineinschreibt, auch die Kopie, die nach einer beschädigten Ankunft noch einmal kommt, gehört also wie bei
  `umask 077 && cat >` nur dem Benutzer. Ein Abbruch genau dann, wenn das Deployment fragt, ob das Board SFTP hat,
  wartet auf das Board und lädt über SFTP hoch, statt das Deployment zu beenden. Und die Sätze der Board-Sitzung, die
  eine KI als externen Text bekommt, nennen das Board mit seinem Label, nie mit seiner Adresse (D0).
- **Ein Datei-Hash und ein Disk-Watchdog** (Architektur-Konsolidierung c1-hashing und c1-disk, Regel R0 des Maintainers:
  zuerst die vorhandene Implementierung). Die 24 Kopien des sha256 einer Datei (15 benannte Helfer, 9 Schleifen; die
  Schleife von `utils/network.py` ist mit diesem Modul gegangen, siehe unten) laufen über das Modul der ältesten,
  `orchestrator/utils/helpers.py` (wo `calculate_file_checksum` war), das eine Hash-Schleife bekommen hat, mit
  optionalen Callbacks für Abbruch und Fortschritt, dem Hashen eines offenen Handles oder seiner ersten Bytes (die
  Prüfung beim Fortsetzen des Deploys), Kopieren beim Hashen (der Kalibrierdatensatz eines Rezepts, gespeicherte
  Artefakte, die Modellkopie der Benchmark-Sandbox), einem Vergleich in konstanter Zeit ohne Groß- und Kleinschreibung,
  einem Hash-Cache mit Lock (der des Benchmark-Tools hatte keinen), dem Schreiber für `SHA256SUMS` und der
  `.sha256`-Begleitdatei. Jeder alte Name bleibt als Delegate mit seinem Fehlerverhalten (ein Fehler, None oder ein
  leerer Text) und seiner Link-Regel; `tests/test_hashing.py` hält jeden fest und lehnt eine neue Hash-Schleife neben
  der einen Implementierung ab. Die Texte und Prüfungen des Disk-Watchdogs sind von der Build-Engine nach
  `orchestrator/Core/disk_plan.py` gezogen (`low_disk_text`, `disk_full_text`, `is_disk_full`, `low_disk_floor`,
  `free_space`, `nearest_existing` und `DiskWatchdog`, das der Container-Watchdog und der Staging-Wächter jetzt sind);
  die Build-Engine behält ihre alten Methodennamen als Delegates, die Texte bleiben gleich und behalten "No space left
  on device", worauf das Self-Healing-Muster (jetzt aus denselben Phrasen gebaut) passt. Die Spielräume bleiben gleich.
- **Eine Speicherplatz-Regel für jede Prüfung des freien Platzes** (Architektur-Konsolidierung c3-disk-callers,
  Entscheidung A4 des Maintainers). Jede Prüfung misst über `orchestrator/Core/disk_plan.py` (`room`, die eine Prüfung
  einer Festplatte, auf der `check_disk` und `require` aufbauen) und lässt die Reserve frei, die Builds frei lassen,
  2 GiB oder 2 % der Festplatte (20 GiB oder 10 % unter einer Dauerfreigabe), neben dem, was angenommene Aufrufe
  irgendeines Prozesses im Ledger des Benutzerordners reserviert haben: die Archiv- und Modell-Downloads der lokalen
  Laufzeit und die Paket-Downloads des Community-Zoos (vorher 256 MiB), die Modellkopie der Benchmark-Sandbox und die
  Umstellung der Wissensbasis (vorher 512 MiB), der Export von Wissens-Snapshots (vorher ohne Spielraum), Mojos
  Speicherplatz-Prüfung (vorher feste 5 und 20 GB; jetzt ein Fehler unter der Reserve und eine Warnung unter der
  doppelten Reserve, in dezimalen GB wie jede andere Größe), die Speicherplatz-Angabe für eine KI und die Prüfungen
  des Deployment-Pakets und der Werkzeuge für den Laufzeit-Build. Ein Download, für den ein laufender Build keinen
  Platz lässt, wird mit den Zahlen und dem nächsten Schritt abgelehnt (noch einmal versuchen, sobald dieser Build oder
  Download fertig ist, oder Speicherplatz freigeben); die Fenster Lokale Laufzeit, Lokale Modelle und Community Hub
  sagen es auf Englisch oder Deutsch, und die Reservierung eines beendeten Programms blockiert nie (das Ledger lässt
  sie fallen). Ein Deployment-Paket hält seinen Platz im Ledger, solange es entsteht, wie es der Hugging-Face-Download
  einer KI tut; hat ein anderer Aufruf den Platz genommen, während die Person gefragt wurde, werden beide auf dieselbe
  Weise abgelehnt und nennen den Download oder das Paket (der Text sagte „this build“). Die Self-Healing-Diagnose nach einer vollen Festplatte nennt weiter die Festplatten unter der Reserve,
  ohne Reservierungen zu zählen, weil sie die Festplatte sucht, die vollgelaufen ist. Der Downloader in
  `orchestrator/utils/download.py` nimmt die Speicherplatz-Regel seines Aufrufers (`space`), so importiert die unterste
  Schicht nichts über ihr. Ein laufender KI-Build trägt ein, was von seiner Reservierung schon auf der Festplatte
  liegt (`ReservationLedger.landed`, über `LandedMeter`: das Wachstum seines bereitgestellten Downloads, seines
  Build-Ordners und seines Zwischenordners, nach jeder Stufe und in jeder Runde seiner Festplatten-Watchdogs), so
  zählen andere Prüfungen diese Bytes nicht mehr doppelt, einmal im freien Platz und einmal in der Reservierung.
- **Die GUI ist ein optionales Extra; Kommandozeile, MCP-Server und Kern laufen ohne Qt.** PySide6 ist das Extra
  `gui` in `pyproject.toml` (die per Hash festgelegte `requirements-gui.txt`, ein Export von `uv.lock` wie
  `requirements.txt`). Die Installer für Linux und Windows installieren es wie bisher; `sudo ./install.sh --headless`
  (`LCF_HEADLESS=1`) und das Framework-Image ohne `--gui` lassen Qt und seine Systembibliotheken weg (das Image ohne
  Oberfläche installiert kein libglib mehr, und sein Build lehnt eine Installation mit Qt ab). Die Texte, die jeder
  Prozess liest (`ui_text`, `tr`, `current_language`, die eine Tabelle `TRANSLATIONS` in
  `orchestrator/utils/localization.py`), importieren kein Qt mehr; das Sprachobjekt des Fensters (`LocalizationManager`,
  `get_instance`) liegt in `orchestrator/gui/localization.py`, und der alte Importpfad findet es weiter. Die GUI startet
  in `orchestrator/gui/app.py`; `orchestrator/main.py` bleibt der Einstieg der Starter und sagt ohne das Extra, wie du
  es installierst, statt eines Tracebacks. Der Katalog der nächsten Schritte liest seine Beschriftungen aus der einen
  Texttabelle statt mit einem zweiten Leser der `l10n_*`-Module. Ein CI-Job führt die Qt-freien Prüfungen und die ganze
  Testsuite ohne PySide6 aus.
- **Qts eigene Schaltflächen sprechen die Sprache des Fensters**: Deutsche Dialoge zeigten Qts
  Standard-Schaltflächen auf Englisch („Close“, „Cancel“, „Yes“, „No“). Das Fenster installiert jetzt Qts eigene
  Übersetzung (`qtbase_de`, mit PySide6 geliefert) mit seiner Sprache und tauscht sie beim Sprachwechsel; ein offener
  Dialog folgt.
- **Der Builder zeigt CPU und Speicher des Build-Containers**, unter dem Fortschrittsbalken, solange ein
  Stufen-Container läuft („Build-Container: CPU 251 % · Arbeitsspeicher 2,0 GB von 8,0 GB“), und das Build-Protokoll
  warnt einmal, wenn der Container 90 % seiner Speichergrenze nutzt (der von Docker oder der VM von WSL2 / Docker
  Desktop), mit dem, was zu tun ist. Die Werte stammen aus dem älteren Build-Weg des Docker-Managers, den nichts mehr
  aufrief; sie misst jetzt der eine Build-Weg (die Build-Engine, `BuildService.live_resources`, gelesen vom
  Orchestrator, mit dem der Build-Dienst arbeitet, wie der Status des Builds), und der Rest des
  älteren Wegs (Start, Überwachung, Stopp, ein Self-Healing-Auslöser und sechs Qt-Signale, auf die niemand hörte) wurde
  entfernt, weil der eine Build-Weg all das tut (Entscheidung Runde 12, Punkt 6). Der Build-Status für die KI bleibt
  gleich.
- **Ein Dienst für Änderungen der KI-Einstellungen, für jeden Kanal** (`orchestrator/llm/settings_change.py`,
  `AISettingsChange`): Die Regeln, die nur der Dialog der KI-Einstellungen kannte (bekannte Provider in der
  Fallback-Kette, kein Cloud-Provider im Offline-Modus, ein Schlüssel ist nötig und kein Admin-Schlüssel, die Form der
  Workspace-ID, die Serveradresse, ein Modell, die Rückfrage bei einem Schlüssel über plain http, der Schlüssel wird
  für seine Adresse gespeichert, die Einstellungen werden geschrieben, die Wissensbasis gestartet und Ditto neu
  geladen), liegen jetzt an einem Qt-freien Ort. Der Dialog behält nur seine Hinweisfenster und seine Rückfrage; die
  Anmeldung und `llmcf ditto login` nutzen dieselben Adressregeln und MCP `update_settings` dieselbe
  Nur-verschärfen-Regel, die aus dem Tool-Modul dorthin umgezogen ist (`direction_problem`, weiter aus
  `orchestrator.tools.tools_status` importierbar). `AISettingsChange.current()` und `with_setting()` sind für die
  Änderung einer einzelnen Einstellung bereit (das kommende `llmcf config set`), und `problems_of_settings()` wendet
  für eine solche Änderung jede Regel an. MCP `update_settings` nutzt es für die beiden KI-Einstellungen, die eine KI
  ändern darf: Offline-Modus mit einem Cloud-Provider wird jetzt mit dem Text des Dialogs abgelehnt (vorher wurde nur
  die Richtung geprüft, sodass eine KI den Offline-Modus einschalten konnte, während ein Cloud-Provider eingestellt
  war), erneut geprüft, wenn die Person geantwortet hat; ein Problem, das die gespeicherten Einstellungen schon haben
  (etwa ein fehlender Schlüssel), lehnt nichts ab. Die Ablehnung nennt Ordner nur mit ihrem Label und Dateien nur mit
  ihrem Namen: Eine unlesbare Provider-Liste (`llm_providers.yml`) zeigt der KI nicht mehr den Pfad des
  Konfigurationsordners.
- **Ein Weg, Builds zu starten, für CLI, GUI und KI-Aufrufer**: `llmcf build start` und **Start Build** im
  Builder-Tab reihen ihren Build über `BuildService.start_build` ein, den Weg, den der MCP-Server und Ditto schon
  nutzten, statt jeweils einen eigenen Auftrag an den Orchestrator zu bauen. CLI und GUI prüfen einen Auftrag deshalb
  gleich, bevor etwas startet: Ein Auftrag ohne Modell, ein lokales Modell, das es nicht gibt (oder das nicht wie ein
  Modell aussieht), ein Kalibrierungsdatensatz, der keine Datei ist, und eine Revision für ein lokales Modell werden
  mit dem Grund abgelehnt (die CLI endet mit Code 2, die GUI zeigt ihn in einem Hinweisfenster), statt später im
  Build zu scheitern. Was jedes Fenster einstellt, bleibt wie es war: Priorität, Optimierung und Zielfrage der CLI,
  Priorität, parallele Builds und Build-Ordner der GUI, ein Target ohne Capabilities wird ungeprüft gebaut, und
  deine eigenen Builds lehnt das Warteschlangen-Limit für KI-Aufrufer nie ab. Die Verfolgungsschleife der CLI liest
  den Build über denselben Dienst. Die Prüfung des Builders auf einen früheren Build mit denselben Optionen erfasst
  jetzt auch ein Target ohne Capabilities (vorher lehnte der Dienst den Vergleich eines solchen Auftrags ab, und der
  Builder vermerkte das nur im Log).
- **Eine Umsetzung je Aufgabe, die ältere, wo sie genügt (Runde 12 der Entscheidungen).** Jeder Punkt der Liste
  unverdrahteter Teile wurde an seiner Geschichte geprüft (`git log -S`) und ist verdrahtet oder mit Nachweis entfernt:
  Die Frage vor dem Öffnen eines Links im Chat ist `bubbles.confirm_open_link` (die ungenutzte Kopie des Panels ist
  weg); die kostenlose Prüfung der Zugangsdaten ist `SignIn.check` (`LLMClient.check_credentials` ist weg, sein Test
  läuft über `SignIn.check`). Die Prüfungen von Python und Docker in Mojo laufen über die ursprünglichen Prüfungen des
  Frameworks in `orchestrator/utils/validation.py`: `validate_python_version` und `check_docker_environment`, das auch
  der Start der Build-Engine nutzt (Befunde und Logzeilen unverändert; seine Frage nach buildx läuft über
  `helpers.execute_command`, schließt also stdin, öffnet unter Windows kein Konsolenfenster und hält den Start, wie das
  Original der Engine, nie an, auch nicht bei einer Ausgabe, die es nicht dekodieren kann). Der Import von
  `validation.py` prüft den Host nicht mehr. Das Modul behält den SSRF-Schutz und die Prüfungen, die kein neuerer Code
  macht, und verliert, was neuerer Code besser kann: die System- und Docker-Berichte (Mojo), die Speicherplatzprüfung
  (die eine Regel für Speicherplatz), die Prüfung von Konfigurationen (`ConfigSchema`), die Regeln für Image- und
  Target-Namen (die des Modulgenerators), den Schreibtest (`ConfigManager.writable_dir`) und `validate_url`. Ältere
  Helfer werden von ihren neueren Kopien wieder genutzt: `helpers.execute_command` führt die Prüfungen von Hardware und
  Toolchain aus, `helpers.safe_json_load` liest die Zustandsdateien der lokalen Laufzeit und des Wissensspeichers,
  `llama_server.managed_endpoint` findet den lokalen Server für den Chat-Client, `LLMOrchestrator.list_workflows` (jetzt
  eine normale Methode) ersetzt das Lesen seiner privaten Tabelle im Hauptfenster, `TargetIn` und
  `TargetCapabilitiesOut` sind die Basis der Modelle des Target-Werkzeugs (`TargetIn` liegt in `schemas/targets.py`, das
  Werkzeug-Ergebnis `TargetCapabilitiesOut` in `tools/tools_targets.py`, deshalb lädt der Import der Target-Werkzeuge
  keinen Tool-Hub und `schemas/` definiert kein Werkzeug-Ergebnis; `framework_tools` behält die Namen), `grant_facts`
  entscheidet
  über die stehende Freigabe eines Build-Plans, `ChatHistory.in_state_dir` lädt den Verlauf des Chat-Panels,
  `PROFILE_FILE_NAME` benennt die Datei der Skript-Prüfung und `TUNINGS` sichert, was `build_info.json` festhält.
  Entfernt: die Texte der v2-GUI und sieben neuere doppelte Texte (EN und DE; der ältere Schlüssel bleibt), die Aliase
  der Menünamen `BUILD_MENU_NAMES` und `TOOLS_MENU_NAMES`, `utils/network.py` (NetworkGuard prüfte 8.8.8.8 und wartete
  ohne Grenze; `PinnedDownloader` setzt fort, versucht es begrenzt erneut und prüft den Pin),
  `scripts/quick_smoke_test.py` (es verlangte Pfade, die es nicht gibt; CONTRIBUTING nennt jetzt `llmcf mojo`, `make
  test` und den Smoke-Test mit einem echten Client) und die Reste `check_command_exists`, `is_admin`, `canonical_key`,
  `_with_message`, `BoardOut`, `is_probe_id`, `SOURCE_KINDS`, `snapshot_listing`, `_write_profile_file`,
  `TokenStore.policy_of`, `deploy_artifact` (Aufrufer nutzen `deploy_package`), `WIDTH_LABELS` und `REQUEST_TTL`.
  CONTRIBUTING (EN/DE) nennt die Regel: erst nutzen, was es gibt.
- **Community-Pakete gehen an das Community-Repository
  [Smilez1985/Edge-LLM-Knowledge-Base](https://github.com/Smilez1985/Edge-LLM-Knowledge-Base)**: Der Maintainer hat es
  gewählt (2026-10-22), und es ist jetzt der Standardwert der Einstellung `community_repo`; „Open pull request“ im
  Export-Fenster und `llm-cli zoo export --pr` übergeben ein Paket also dorthin, statt mit „No community repository is
  configured“ abzubrechen. Sein Beitrags-Workflow, sein signierter Index und der Schlüssel des Maintainers sind noch
  nicht veröffentlicht: `community_index_url` bleibt leer, bis in diesem Repository der erste signierte Index liegt;
  der Zoo installiert deshalb weiterhin nur Paketdateien. Leerst du die Einstellung selbst, wird wie bisher nichts
  übergeben. Ein Test hält den Standardwert fest und prüft ihn gegen die Regel der Einstellung und die der Übergabe.
- **Das Repository und die Kennungen des Produkts tragen den Namen LLM Cross-Compiler Framework.** Das Repository
  heißt jetzt `Smilez1985/LLM_Cross-Compiler_Framework` (GitHub leitet die alte Adresse
  `Smilez1985/llm_conversion_framework` weiter), und was noch nach dem LLM Conversion Framework benannt war, folgt:
  die Links im User-Agent des Crawlers, im Fehlerbericht und im Workflow des Community-Repositorys; die
  Installationsordner (Linux `/opt/llm-cross-compiler-framework`, Windows
  `C:\Program Files\LLM-Cross-Compiler-Framework` mit der Desktop-Verknüpfung **LLM-Cross-Compiler-Framework**, und
  der Programmordner des Framework-Images); die Python-Distribution `llm-cross-compiler-framework`; die
  Windows-Skripte `scripts/Uninstall-LLM-Cross-Compiler-Framework.bat` und
  `scripts/start-llm_cross_compiler_framework.bat`; das Produkt-Token des Crawlers `LLM-Cross-Compiler-Framework-Bot`
  (Gruppen in der robots.txt, Bereiche im `X-Robots-Tag` und Meta-Tags, die für `LLM-Conversion-Framework-Bot`
  geschrieben wurden, gelten weiter, ein früher erklärter Nutzungsvorbehalt einer Website bleibt also bestehen); und
  der Schlüsselbund-Dienst `llm-cross-compiler-framework` (Tresorschlüssel, der Audit-Schlüssel und die
  Revisionsanker, die noch unter `llm-conversion-framework` liegen, werden gelesen und beim ersten Gebrauch unter den
  neuen Namen kopiert, die alten Einträge werden nie entfernt, und der gespeicherte Prüfwert des Tresors behält seinen
  Text, sodass jeder vorhandene Tresor aufgeht). Wie eine vorhandene Installation umzieht: Eine Installation im alten
  Ordner wird nie verändert oder gelöscht. `install.sh` kopiert die Dateien von dir aus ihrem `configs/`, die die neue
  Installation noch nicht hat (`config.yml`, eine selbst geschriebene `framework.yml` und `secrets.store`, den Tresor
  für Zugangsdaten einer Version vor 3.0.0, den das Framework dann übernimmt; nie über eine vorhandene Datei, nie über
  einen Link), sagt, was es nicht übernommen hat (Builds, Cache, Modelle, eigene Targets und Packs), dass über MCP
  verbundene KI-Apps die alte Installation starten, bis sie aus der neuen eingerichtet sind, und wie man den alten
  Ordner entfernt, nachdem man hineingesehen hat. `install.bat` nennt den alten Ordner, die KI-Apps und den
  Uninstaller, der jetzt beide Installationen findet, für jede zeigt, was er entfernt, und vorher fragt (ohne Antwort
  wird nichts entfernt). `make` behält einen Container von vor der Umbenennung, solange er Dateien
  von dir im alten Programmordner enthält. Siehe
  [README: Eine Installation von vor der Umbenennung](README_DE.md#eine-installation-von-vor-der-umbenennung).
- **Schnellerer Start: schwere Teile laden erst, wenn sie gebraucht werden** (unveröffentlicht; Entscheidung des
  Maintainers RG1, Lazy Loading): `llm-cli`, der MCP-Server und die GUI importierten beim Start das ganze Framework.
  Jetzt importiert ein CLI-Befehl das Framework, die Build-Engine und die Manager erst, wenn er sie nutzt; das
  Framework importiert seine Manager beim Hochfahren und die Wissensbasis erst, wenn sie eingeschaltet ist; Qt wird
  außerhalb der GUI nur geladen, wenn ein Signal des Docker-Managers genutzt wird; der Crawler (mit seinen Parsern für
  HTML und PDF), numpy, die Prüfungen von Mojo, die Anbieterschicht von `orchestrator.llm` und die Teile von
  `orchestrator.runtime` laden bei ihrer ersten Nutzung, auch bei eingeschalteter Wissensbasis; `--help` lädt kein
  rich. Die Host-Prüfung, die beim Import der URL-Prüfungen lief, nahm eine CPU-Stichprobe von einer Sekunde und
  suchte zwei Programme, deren Ergebnisse sie nie zeigte; sie führt jetzt nur die Prüfungen aus, deren Fehler sie
  meldet (Platte, Speicher, Python, Plattform), daher erscheint ihre Warnung zum Plattenplatz bei der ersten Nutzung
  der URL-Prüfungen und nicht bei jedem Start. Gemessen auf derselben Maschine mit einem temporären Home, vorher und
  nachher, in abwechselnden Läufen (Median aus 5): `llm-cli --help` 3,15 s und 140 MB auf 0,25 s und 27 MB;
  `llm-cli mcp serve`, bis es auf `initialize` antwortet, 4,77 s und 191 MB auf 2,67 s und 118 MB (2,69 s und 118 MB
  bei eingeschalteter Wissensbasis); das Hauptfenster der GUI 3,97 s und 210 MB auf 2,66 s und 177 MB. Das
  Hochfahren prüft Docker weiter wie bisher, daher laden `mcp serve` und die GUI weiter das Docker-SDK. Befehle,
  Ergebnisse und Meldungen bleiben gleich. Ein Teil des Frameworks, der sich nicht importieren lässt (eine kaputte
  Installation), beendet das Hochfahren jetzt mit seiner Ursache wie andere Fehler beim Hochfahren: Die CLI gibt
  „Failed to initialize framework: ...“ aus und endet mit 1 statt mit einem Traceback, und `--help` funktioniert
  weiter. Tests starten die CLI, `mcp serve` (auch bei eingeschalteter Wissensbasis) und die GUI in einem frischen
  Interpreter und prüfen, dass Crawler, die Bibliotheken für Embeddings, die SDKs der Anbieter, Qt, die Prüfungen
  von Mojo (auch seine Prüfung der Boards), der Speicher der Wissensbasis und die private Brücke zum offenen Fenster
  nicht geladen werden und kein Lizenztext der Wissenspakete gelesen wird; der MCP-Server lädt die Brücke beim ersten
  Aufruf seines Werkzeugs `gui`, die GUI startet sie mit ihrem Fenster.
- **Roadmap: Rust dort, wo das Framework auf das Netzwerk oder ein Board trifft** (unveröffentlicht; Entscheidungen
  des Maintainers RG2 bis RG4): Die Roadmap im Blueprint plant das Netzwerk-Gateway für MCP in Rust mit dem
  MQTT-Broker rumqttd darin (statt des im Python-Prozess geplanten amqtt-Brokers), den Root-Helfer auf dem Board als
  statisches Rust-Programm und die Rust-Teile des Frameworks als fertig gebaute, mit Sigstore signierte
  Release-Dateien, mit ihren Prüfsummen im Repository und selbst bauen als Opt-in. Sie hält außerdem fest: keine
  fertigen Docker-Images für Targets, kein Go, und die GUI bleibt Qt.
- **Die lokale Wissensbasis liegt in Qdrant Edge** (DECISIONS RG5): Die Rust-Engine von Qdrant läuft im
  Framework-Prozess (neue Abhängigkeit `qdrant-edge-py` 0.8.0, Apache-2.0, exakt gepinnt; Wheels für Linux x86_64 und
  aarch64, Windows x86_64 und macOS, ein Wheel für jedes Python ab 3.10) statt des lokalen Modus von qdrant-client,
  einer Python-Nachbildung für die Entwicklung. Die Daten liegen jetzt in `~/.llm-framework/qdrant_edge`. Collections
  je Embedding-Modell, IDs, Payloads, Filter, das Ersetzen einer Quelle, der Reranker, das Budget für Auszüge und die
  Schnappschüsse verhalten sich wie bisher; die dichte Suche bleibt exakt, und die verbundenen Werte bleiben gleich
  (RRF k = 2). Den Speicher öffnet immer nur ein Prozess: die Sperrdatei `qdrant_edge.lock` des Frameworks verhindert,
  dass GUI, CLI und MCP-Server gleichzeitig schreiben; ein zweiter Prozess wartet bis zu 5 Sekunden und bekommt dann
  einen klaren Fehler "in use". Eine ältere Wissensbasis wird bei der ersten Nutzung umgewandelt: Vektoren und Texte
  werden kopiert (nichts wird neu eingebettet), Anzahl und eine Stichprobe von Punkten und Suchen werden gegen den
  alten Speicher geprüft, und die Umwandlung wird in `knowledge_conversion.json` festgehalten; der alte Ordner `qdrant`
  bleibt, bis du ihn löschst. Eine fehlgeschlagene Umwandlung lässt den alten Speicher arbeiten und sagt, warum. Nach
  der Umwandlung springt der alte Ordner nie wieder ein: Lässt sich Qdrant Edge später nicht laden (ein fehlendes oder
  kaputtes Paket), wird die Wissensbasis mit dem Grund abgelehnt, und Mojo Oracle meldet einen Fehler, statt still den
  alten Ordner zu nutzen, dem fehlt, was seitdem dazukam. Zweimal gemessen mit 50 000 künstlichen Auszügen: eine Suche
  dauert 12 bis 14 ms statt 2,5 bis 4,9 s, das Öffnen etwa 1 s statt 11 bis 19 s, der Speicher 410 bis 520 MB statt
  1,6 bis 2 GB; die Umwandlung dauerte 68 bis 159 s (5 000 Auszüge: 13,5 s). qdrant-client bleibt für einen
  optionalen Qdrant-Server (`qdrant_url`) und um den alten Speicher zu lesen. Qdrant Edge hat keinen Build für
  Windows auf ARM; dort behält die Wissensbasis den lokalen Modus von qdrant-client, und Mojo Oracle sagt es.
- **Wissens-Schnappschüsse nennen den Speicher, aus dem sie kommen (Format 2)**: Ein Schnappschuss bleibt eine
  portable Datei, für jeden Speicher gleich. Schnappschüsse im Format 1, von früheren Versionen geschrieben, lassen
  sich in Qdrant Edge wiederherstellen und werden so umgewandelt; ein neueres Format, ein Vektor der falschen Dimension
  oder ein kaputter Stichwort-Vektor wird abgelehnt, bevor sich etwas ändert.
- **Die Prüfung auf gleiche Builds blockiert das Builder-Fenster nicht mehr** (unveröffentlicht; achte Runde, N11):
  Vor einem Build fragte die Prüfung Hugging Face im Thread des Fensters nach dem aktuellen Commit des Modells, daher
  reagierte das Fenster bei langsamem Netz nicht (bis zu 10 Sekunden). Die Abfrage läuft jetzt im Hintergrund; Start
  wartet so lange, und das Formular bleibt bedienbar. Der Build übernimmt genau die geprüften Werte.
- **Builds im alten Standardordner behalten ihre ID, und Pakete werden dort gefunden** (unveröffentlicht; achte Runde,
  Antwort 3): Seit der Einstellung für den Ausgabeordner ist der Build-Ordner des Frameworks nicht mehr der
  Ausgabeordner, daher bekam ein Build dort eine neue ID, und das KI-Tool `deploy_to_device` fand die Deploy-Pakete
  darin nicht mehr über ihre ID. Die frühere ID eines Builds erreicht ihn weiter (Feed-Zeilen, offene Freigaben und
  KI-Aufrufer, die sie behalten haben), außer ein anderer Build hat diese ID jetzt. Pakete werden zuerst im
  Ausgabeordner gesucht, dann im Build-Ordner und in früheren Ausgabeordnern, die du eingestellt hattest.
- **`get_status` nennt den Datenträger des Build-Ordners** (unveröffentlicht; achte Runde, Antwort 3): Die Angabe zum
  Speicherplatz nannte nur den Cache und den Ausgabeordner, nicht den Build-Ordner, in den ein Build zuerst schreibt.
  Jetzt nennt sie alle drei; Ordner auf einem Datenträger werden zusammen genannt, nur mit ihrer Bezeichnung.
- **Die Tools, die Text speichern, sagen, wann das Suchmodell zuerst heruntergeladen wird** (unveröffentlicht;
  next-fixes 119): Die Situationswarnung `embedding_model_download` war erklärt, erschien aber nie. Jetzt prüfen
  `crawl_docs_site`, `index_framework_docs` und ein Wissens-Pack über `add_pack_from_index`, ohne etwas herunterzuladen
  oder zu laden, ob die Modelle der Wissensbasis auf diesem Rechner liegen. Ihre Frage sagt es, mit der Größe und der
  Warnung, wenn ein Download nötig ist oder sich nicht ausschließen lässt. Ein MCP-Prompt mit Auszügen aus der
  Wissensbasis startet den Download nie: Wie `search_knowledge` liest er nur mit den Modellen auf diesem Rechner und
  sagt, wenn sie fehlen. Dittos automatische Diagnose eines gescheiterten Builds, nach der niemand gefragt wird, liest
  genauso.
- **Reparaturhinweise für KI-Aufrufer nennen Mojo** (unveröffentlicht; Runde 7 Punkt 11'): Ablehnungen und Fehler, die
  KI-Aufrufer lesen, schickten sie zu `llm-cli doctor`. Jetzt nennen sie das Tool `run_mojo_oracle` (oder `llmcf mojo`
  im Terminal). Der Hinweis auf fehlende Builder-Images behält `llm-cli doctor --fix --tier 2`, das sie baut.
- **Dittos alte Laufzeit-Art verschwindet ganz** (unveröffentlicht; W2 vom 2026-10-16, Runde 7 D): Nach einem
  Wechsel zwischen direkt und Docker standen nur die Build-Ordner, Images und Container der alten Art in der Liste, und
  die Build-Ordner kamen in den Papierkorb. Jetzt steht jedes Objekt der alten Art in der Liste, auch ihre Einträge in
  `mode.json` und `active.json` und das Log der Image-Builds, und die Liste wird endgültig gelöscht, nicht in den
  Papierkorb, sodass von der alten Art nichts bleibt. Sie wird erst angeboten, wenn die neue Art funktioniert, und das
  Entfernen lehnt ab, solange sie das nicht tut. Eine Frage bestätigt die ganze Liste („Nein“ als Standard).
- **Die Adressprüfungen sind sich bei IPv6 in Klammern einig** (unveröffentlicht): `deploy run --host [fe80::1]`
  bestand die Prüfung der CLI, aber das Deployment lehnte die Adresse ab. Jetzt lehnt jede Stelle, die eine
  Board-Adresse nimmt (Deployment, `scan --device`, `deploy devices add`, die Prüfung und das Deployment-Fenster der
  GUI), eine IPv6-Adresse in Klammern ab, bevor etwas gesendet wird. Schreib sie ohne Klammern (`fe80::1`); der Port
  hat eine eigene Option.
- **Der Benutzerordner wird beim Anlegen privat gemacht** (next-fixes 118): Jedes Lesen der Einstellungen setzte
  `~/.llm-framework` wortlos auf Modus 0700, daher erschien Mojos Befund `state.permissions` fast nie. Jetzt bekommt der
  Ordner 0700, wenn das Framework ihn anlegt; ein vorhandener Ordner behält seinen Modus, Mojo Oracle meldet einen, den
  andere lesen können, und Mojo Miracle repariert ihn nach deinem Ja.
- **Ein Deployment auf ein Board behält jeden älteren Laufzeit-Build** (Runde 5 f): `llm-cli device runtime deploy`
  und das KI-Tool `deploy_device_runtime` entfernten alle älteren Laufzeit-Builds auf dem Board außer dem vorigen, ohne
  Frage. Jetzt bleibt jeder ältere Build. Das Ergebnis sagt, wie viele geblieben sind und wie groß sie sind, und
  `llm-cli device runtime prune GERÄT --on-device` listet sie für dich: voller Pfad auf dem Board, Ordnername, Größe
  und was jeder ist, nichts angekreuzt, „Nein“ als Standard. Nur die Builds, die du ankreuzt, werden entfernt,
  endgültig (ein Board hat keinen Papierkorb), nach einem Audit-Eintrag nur mit den Ordnernamen. Der aktive Build und
  einer, den ein laufendes Programm nutzt, stehen nie in der Liste. Eine KI liest nur, wie viele Builds geblieben
  sind und dass nur du sie entfernst. Wird ein Build erneut deployt, der schon auf dem Board liegt, bleibt er dort,
  auch wenn seine Prüfungen scheitern.
- **Der Ausgabeordner ist `~/llmcf/output`; der alte Ausgabeordner ist der Build-Ordner des Frameworks** (achte
  Runde, Antwort 3): die Einstellung `output_dir` nennt jetzt den Ordner, in dem fertige Modelle gespeichert werden
  (Standard `~/llmcf/output`, bisher `output/` der Installation oder des persönlichen Ordners). Dieser alte Ordner ist
  die neue Einstellung `build_dir`: GUI-Builds warten dort, bis sie gespeichert sind, und jeder frühere Build bleibt
  dort und wird weiter aufgelistet. CLI-Builds ohne `--output` legen keinen Ordner `build_<Datum>_<Uhrzeit>` mehr an
  (ein späterer Build kommt stattdessen neben einen früheren). Das Container-Image setzt
  `LLM_FRAMEWORK_BUILD_DIR=/data/builds`.
- **Der Builder löscht keine Images mehr** (veröffentlicht in 3.0.0; Runde 7 Punkt 12): Jeder Build endete damit, sein
  Image zu entfernen und die verwaisten Images des Frameworks zu prunen. Jetzt heißt das Image des Builds
  `llm-framework/<Target>:last-build` (der nächste Build dieses Targets nutzt es wieder), und nur sein Name für den
  einzelnen Build wird entfernt, nachdem der neue Name am Image zu sehen ist. Das Image, das diesen Namen vorher hatte,
  bleibt ohne Namen und wird dir zum Löschen gelistet (Tools > Framework Build Images..., `llm-cli doctor --fix --tier
  2`). Nichts im Framework prunt Docker-Objekte; Docker-Images werden nur gelöscht, wenn du sie anhakst.
- **Keine Vorgabe nennt mehr ein Image aus einer Registry** (Runde 5 h, sechste Runde): Das Framework veröffentlicht
  keine Images, daher nennen die Vorgaben lokale Images. `image_inference_runtime` ist `llm-framework/inference:local`;
  ein Deployment-Paket nimmt es erst, wenn du es auf diesem Rechner gebaut hast, wie jedes Image ohne Digest.
  `docker_registry` ist leer (nichts liest es). Das experimentelle Benchmark-Fenster nutzt
  `llm-framework/builder:local` und hält mit einer Meldung an, wenn dieses Image hier fehlt, statt Docker es ziehen zu
  lassen. Der Block `docker` in `targets/_template/target.yml` und `targets/Rockchip/target.yml` nennt ein lokales
  Image ohne Registry (`registry: ""`); Kommentare sagen, dass nichts `registry` oder `tags` liest.
  `prebuilt_llama.KNOWN_PREBUILT_IMAGES` behält die früheren `ghcr.io`-Namen als Liste zum Erkennen.
- **uv ersetzt Poetry für die Festlegung der Abhängigkeiten** (sechste Runde, Entscheidung 14): `pyproject.toml`
  nutzt die Standardtabelle `[project]` (die Entwicklungswerkzeuge in `[dependency-groups]`), `uv.lock` ersetzt
  `poetry.lock`, und `requirements.txt` und `requirements-dev.txt` sind mit Hashes festgelegte Ausgaben von
  `uv export`, deren erste Zeilen den Befehl nennen. Keine Version hat sich geändert: Alle 121 Festlegungen sind die
  der bisherigen `poetry.lock`; die Exporte lassen nur die Hashes von Wheels für Python-Versionen außerhalb von 3.11
  bis 3.14 weg. Die Installer installieren weiter die mit Hashes festgelegte `requirements.txt` des ausgecheckten
  Frameworks und bestehen jetzt auf den Hashes (`--require-hashes`, kein Ausweichen auf nicht festgelegte Pakete);
  `install.bat` bricht ab, wenn pip scheitert, statt weiterzumachen. Dependabot aktualisiert `uv.lock`, der neue
  CI-Job "Lock files" prüft, dass `uv.lock` und beide Exporte zusammenpassen, und `make test` und die
  Entwicklungsumgebung nutzen uv (`uv sync`).
- **Rückfragen zeigen eine Kurzfassung und die Arten der Hinweise** (Delegationsentwurf 4.5, LAY-L2): Eine Rückfrage
  im eigenen Fenster des Frameworks (Dittos Dialog, die CLI-Abfrage, das Freigabefenster) beginnt nach ihrer ersten
  Zeile mit 3 bis 6 Zeilen in der Sprache der Person (Englisch oder Deutsch): was passiert, mit sprechenden Namen, wie
  viel heruntergeladen wird und wie viel Speicherplatz nötig ist, „Braucht deine Entscheidung, weil: ...“ mit jedem
  Sicherheits- und Rechtshinweis, die abzuwägenden Situationshinweise, eine sicherere Wahl und wer entscheiden darf.
  Die Kurzfassung entsteht nur aus berechneten Zahlen, den Hinweisen und sprechenden Namen, nie aus Text aus einem
  Repository, einem Log oder von einer KI. Jede Rückfrage, auch ein MCP-Formular, nennt nach den geprüften Fakten
  „Situation:“ und „Needs you (security / legal):“.
- **Abgelehnte MCP-Aufrufe tragen ihren Grund-Code als strukturierten Inhalt** (`{"error": {"code", "field",
  "blocker"}}`, nur Fakten des Frameworks, der Text unverändert; `blocker` nennt das harte Hindernis aus dem
  Faktenkatalog, das den Aufruf vor jeder Rückfrage abgelehnt hat, etwa `disk_full`). Ein Client liest Code und Feld
  daraus, statt sie aus den Worten zu raten; ein unbekannter Code wird als `failed` gesendet, und ein Feld, das kein
  einfacher Argumentname ist, wird weggelassen.
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
  `ghcr.io/smilez1985/llm-runtime` zu holen: Katalogmodelle (Dittos eigene) nativ oder in der Docker-Sandbox; jedes
  andere Modell, etwa ein für ein Board umgewandeltes, nur in der Docker-Sandbox (das eigene, lokal gebaute Image des
  Builds, ohne Netzwerk), nie nativ auf diesem Computer. Eine gescheiterte Messung ist ein Fehler mit Grund statt
  der Werte 999.99 oder 888.88.
- **MCP-Ergebnisse**: Der Textblock eines Tool-Ergebnisses beginnt mit einem Satz, der nur aus Fakten des Frameworks
  besteht (IDs, Zustände, Anzahlen), gefolgt vom JSON, eingepackt als nicht vertrauenswürdige Daten;
  Ausgabeschemas markieren jedes Feld mit externem Text (`"untrusted": true`); Ergebnisse tragen `notes`, die das
  Framework schreibt (zum Beispiel „die Wissensbasis ist erreichbar; kein Auszug passte“ oder dass nur die Builds
  dieses Prozesses aufgeführt sind); `suggested_step` eines offenen Self-Healing-Vorschlags heißt jetzt
  `manual_step_text`, Text zum Lesen für die Person. Die Serveranweisungen sagen, wie markierte Felder zu behandeln
  sind und dass der Client eine Aufgabe, die er nicht erledigen kann, mit dem konkreten Grund meldet.
- **MCP-Clients sehen nur die Tools, die sie nutzen dürfen**: Tools, die deine Berechtigungen verweigern, fehlen in
  der Tool-Liste (dieselben Tools, die Ditto bei denselben Berechtigungen bekommt); ein Aufruf eines solchen Tools
  wird trotzdem abgelehnt und festgehalten. `destructiveHint` folgt der eigenen Angabe jedes Tools statt seiner Stufe
  (`resolve_healing` bleibt als zerstörend markiert: es ändert eine Einstellung und wiederholt einen Build), und
  Tools, die andere Geräte erreichen, bitten den Client, bei jedem Aufruf seine eigene Rückfrage zu zeigen
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
  Frage erlaubt werden, und ein Speicherlimit von 0, das Docker als keins liest, zählt als Lockerung;
  `crawler_download_pdfs` nur ausschaltbar; Einstellungen, die dich darüber informieren, was KIs tun, etwa gesprochene
  Ansagen, kann keine KI ändern, und `update_settings` wird nicht registriert, wenn es eine davon erreichen könnte;
  Berechtigungen, Geheimnisse, Tresor, Einstellungen des KI-Anbieters, Adressen und Ordner vor der Frage abgelehnt;
  kein Wert wird als „Standard“ gelesen).
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

- **Der Reiter „AI Chat“ ist in den Chat-Bereich umgezogen**: Das Hauptfenster hat den Reiter nicht mehr; alles,
  was er konnte, funktioniert im Bereich (Dittos Runden in einem Worker-Thread, der Bestätigungsdialog für Dittos
  Tools, die Zeile mit dem Zugang, das gespeicherte Gespräch in `chat_history.json`, das einmal in den Chat-Verlauf
  übernommen wird). `gui/chat_window.py` behält `ChatWorker`, `describe_access` und `ChatWindow` (jetzt der Bereich)
  mit den alten Attributnamen. Enter sendet, Umschalt+Enter fügt eine Zeile ein. Die Hinweise der Statusleiste zu einem
  Korrekturvorschlag eines KI-Clients und zur Nutzung einer Dauerfreigabe kommen auch im Chat an.
- **„Chat leeren“ zählt auf, was es löscht, bevor es fragt**: Das frühere „Clear History“ löschte Dittos ganzen
  Chatverlauf nach einem bloßen „Sind Sie sicher?“. Die Frage nennt jetzt für Dittos Gespräch (`chat_history.json`,
  so wie die Datei es jetzt hält, auch wenn ein anderes Fenster oder das Terminal es inzwischen gespeichert hat) und
  für die Gesprächszeilen des Chat-Verlaufs den vollständigen Pfad, den Dateinamen, die Größe und was es ist, sagt,
  wie viele Einträge darüber, was KI-Apps getan haben, nur ausgeblendet (nie gelöscht) werden, und hat „Nein, alles
  behalten“ als Vorgabe. Beide Teile werden ins Audit-Log geschrieben (`feed_cleared`, mit dem Hash des Gezeigten),
  bevor etwas gelöscht wird. Nichts wird gelöscht ohne Audit-Log (auch ohne Chat-Verlauf), wenn sich der Chat oder
  Dittos Gespräch geändert hat, während du gefragt wurdest, oder wenn das Fenster ein Gespräch hält, das seine Datei
  nicht zeigt.
- **Der Doctor ist jetzt Mojo** (veröffentlicht in 3.0.0; sechste Runde, Entscheidung 9): Der Code liegt jetzt in
  `orchestrator/Core/mojo/` (`orchestrator/Core/doctor.py` exportiert jeden Namen weiter, den es hatte); Befundcodes,
  Exit-Codes und JSON-Schlüssel bleiben, und das JSON nennt je Befund zusätzlich `where`, `why`, `repair_id` und
  `cannot`. `llm-cli doctor` behält seine Optionen und seine Ausgabe und schreibt einen kurzen Hinweis auf stderr, der
  `llmcf mojo` nennt. `doctor --fix` macht jetzt die protokollierten harmlosen Reparaturen von Mojo Miracle statt
  einer nicht festgehaltenen Änderung der Rechte des Ordners und sagt, warum ein Problem noch da ist (zum Beispiel:
  Das Audit-Log kann die Reparatur nicht festhalten, also wurde nichts geändert); `--tier 2` zeigt weiter die Liste
  der Build-Images des Frameworks. Das Tool `run_doctor` bleibt aufrufbar, wird aber nicht mehr aufgelistet; eine
  Einstellung für `run_doctor` oder `run_mojo_oracle` gilt für beide, und die strengere gewinnt. `make mojo` neben
  `make doctor`.
- **Wissenspakete enthalten den vollen Text einer Seite nur, wo ihre Lizenz es erlaubt, mit den Lizenztexten, die sie
  verlangt** (achte Runde, Q-1 und Q-1'; W3 vom 16.10.2026): Ein Wissenspaket und der Wissens-Snapshot, den der
  Modul-Assistent einem Target beilegt, behalten den vollen Text einer Seite nur unter einer Lizenz, die die
  Weitergabe erlaubt (CC0, Gemeinfreiheit, CC BY, CC BY-SA, MIT, Apache-2.0, BSD, ISC, 0BSD, Unlicense, MPL-2.0), mit
  der verlangten Namensnennung (Titel, Adresse, Urheberrechtshinweis, Lizenz, die vorgenommenen Änderungen). Eine
  Seite mit Urheberrechtshinweis ohne eine solche Lizenz, Code ohne Lizenz, eine NC-, ND- oder Copyleft-Lizenz, eine
  nicht erkannte Lizenz und eine Seite ganz ohne kommen als ihre Adresse mit kurzen Fakten hinein (Titel,
  Abschnittsnamen, Fachbegriffe); wer das Paket bekommt, crawlt sie unter eigenen Prüfungen. Bisher kam jede Seite mit
  vollem Text hinein. Seiten mit vollem Text unter einer Lizenz, die ihren Text oder Erlaubnishinweis verlangt (MIT,
  BSD, ISC, Apache-2.0, MPL-2.0, CC BY, CC BY-SA), kommen mit dem Text dieser Lizenz in `LICENSES/<SPDX-ID>.txt` und
  einer `NOTICE` (in einem Target in `knowledge/`). Die Texte kommen unverändert aus der SPDX License List 3.29.0
  (Daten unter CC0) in `orchestrator/data/licenses/` mit, jeder über seinen sha256 geprüft; die Datenschutzbereinigung
  ändert sie nie. `NOTICE` führt jede Seite mit ihrer Quelladresse, ihrer Copyright-Zeile wie gefunden und ihrer
  SPDX-ID auf; bei MIT, BSD und ISC steht die Copyright-Zeile beim Lizenztext (die Lizenzdatei eines Repositorys und
  Code von GitHub, der seine Zeile daraus bekommt, führen jede Copyright-Zeile dieser Datei auf), und bei Apache-2.0
  folgen die Hinweise aus der `NOTICE`-Datei des Werks, wenn der Crawl sie gelesen hat. Eine Seite, deren Lizenz ihren
  Text verlangt und für die das Framework keinen kanonischen Text hat, kommt als ihre Adresse mit kurzen Fakten hinein
  und sagt warum (Zustand `no_licence_text`). Die Installation, `zoo verify` und die Prüfung des Community-Repositorys
  lehnen ein Paket ab, dessen Lizenzdateien nicht zu seinen Seiten passen (die Lizenztexte eines Target-Pakets auch
  ohne Snapshot); ein installiertes Paket behält sie. Ein Target-Export lässt Lizenzdateien draußen, die `knowledge/`
  ohne Snapshot enthält, und sagt das. Die Zusammenfassung des Exports führt jede Seite auf, mit dem, was sie enthält,
  und warum, und nennt jeden Lizenztext und warum (CLI, GUI; `export_pack` nennt Lizenz-IDs, Dateien und
  Seitenzahlen), ein Hinweis zählt beides, und ein Target-Export wendet die Regel auch auf einen älteren Snapshot an.
  Der einmalige Hinweis vor Deep Ingest sagt das, deshalb erscheint er noch einmal.

### Veraltet
- `--password WERT` bei `deploy run`, `scan --device` und `device runtime deploy`: Jeder Benutzer sieht den Wert in
  der Prozessliste, und er bleibt im Shell-Verlauf. Es funktioniert weiter und gibt eine Warnung aus; nimm `--password`
  ohne Wert (verdeckte Abfrage) oder `--password-stdin`.
- `llm-cli secrets set --password`: Ein Wert auf der Kommandozeile landet im Shell-Verlauf und in der
  Prozessliste. Nutze die verdeckte Abfrage oder `--with-token`.
- `llm-cli build start --parallel`: Die Option hatte nie eine Wirkung (ein Aufruf startet einen Build). Sie ist in
  der Hilfe ausgeblendet und gibt eine Warnung aus. `--optimization` bleibt; seine Hilfe sagt jetzt, dass das
  Rockchip-Target die Option nicht auswertet.
- `DeploymentManager.trust_host` (DECISIONS Runde 22, P5): der alte Schreiber eines bestätigten Host-Schlüssels, ohne
  Audit-Zeile, den nichts im Framework aufruft. Er ist jetzt ein schlanker Delegate an `orchestrator/remote/pairing.py`
  `trust_presented_key`, den einen Schreiber, der den Schlüssel unter seiner Sperre speichert und im selben Schritt die
  Audit-Zeile des Vertrauens schreibt. Nur die Person kann ihn nutzen: Er nimmt den `pairing.PersonChannel` des Kanals
  (`cli` oder `gui`, mit dem Audit-Log), den nur die Kanäle der Person bilden können, weil kein Modul, das eine KI
  erreicht, `pairing` importiert. Ohne ihn vertraut er nichts und wirft `PermissionError`; jeder Aufruf warnt mit
  `DeprecationWarning`. Die Prüfung in `tests/test_remote_trust.py` ist jetzt strenger: das Deploy-Modul nennt
  überhaupt keinen Vertrauens-Schreiber mehr.

### Entfernt
- Der ungenutzte `LocalProbeWorker` des Builder-Tabs, ein zweiter Starter des Fallback-Probe-Skripts (ein eigener
  Subprocess-Aufruf ohne Zeitlimit, mit geerbtem stdin, im Arbeitsordner der GUI). Jeder lokale Scan
  startet das Skript über `HardwareProbes.run_local` (den einen Starter, den `llmcf scan --script` und das
  Scan-Menü nutzen); seine Tests sind dorthin umgezogen.
- Die veraltete Kopie `Docker Setup/pyproject.toml` (Version 1.6.0, von nichts gelesen): Das Container-Image entsteht
  aus der `pyproject.toml` und der `requirements.txt` des Repositorys selbst.
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
  Wissensbasis nutzt ein eingebettetes Qdrant), und der Qdrant-Beiwagen der Gerätepakete: Ein Paket mit `--docker`
  und eingeschalteter Wissensbasis bringt das Qdrant-Image, einen leeren Ordner `data/qdrant` und `QDRANT_HOST` nicht
  mehr mit, weil nichts auf dem Gerät sie nutzte (die Wissensbasis dient Ditto auf diesem Computer), und das
  Deployment-Fenster bietet „Qdrant Sidecar“ nicht mehr an. Die Einstellung `image_qdrant` wird mit einer Logzeile
  ignoriert.
- Ungenutzter Code: `orchestrator/Core/transparency_manager.py` (RepE: es brauchte ein transformers-Modell im eigenen
  Prozess des Frameworks, das weder der verwaltete llama-server noch ein Cloud-Anbieter liefert) und
  `scripts/ci_image_audit.sh` (von nichts aufgerufen; es startete ein nicht festgelegtes `dive`-Image mit
  eingebundenem Docker-Socket). Der Docker-Workflow schreibt stattdessen die Größe des Rockchip-Images und seine
  Schichten in die Job-Zusammenfassung, mit dem eigenen docker-Befehl des Runners.

### Behoben
- **Alte Hugging-Face-Downloads entfernen geht mit dem echten Audit-Log**: Das Audit-Log lehnt Ergebnisse ab, die es
  nicht kennt, und das Löschen alter Downloads (Übernehmen, `hf_takeover.delete_old_copies`) schrieb
  `old_downloads_deleted`, das es nicht kannte; das Löschen stoppte also mit „The audit log cannot record the
  deletion“ (die Tests nutzten ein Ersatz-Log). Es ist jetzt ein bekanntes Ergebnis, ebenso `earlier_build_replaced`
  von „Nehmen“.
- **hadolint-Funde beim Build werden gezeigt statt weggeworfen** (DECISIONS Runde 23, Punkt 4): Der Build meldete
  jedes Ergebnis seiner Dockerfile-Prüfung als „Hadolint check skipped (tool not found or failed)“ und verwarf, was
  hadolint fand. `BuildEngine._validate_dockerfile_hadolint` führt hadolint jetzt über das neue
  `orchestrator/utils/hadolint.py` aus (im Ordner des Dockerfiles mit seinem Dateinamen, ohne Farben, höchstens 60 s)
  und unterscheidet: nicht installiert (ein Hinweis im Build-Log, woher du es bekommst, keine Warnung mehr), Funde
  (eine Zusammenfassung, dann jeder Fund in klaren Worten, etwa „hadolint, Dockerfile: line 30, DL3008 (warning):
  ...“, im Build-Log und als Fortschrittswarnung, höchstens 10 Warnungen), keine Funde (steht im Log) und ein Lauf
  ohne Ergebnis (Absturz, Zeitüberschreitung: eine Warnung mit dem, was hadolint schrieb). Die Prüfung bleibt ein
  Hinweis: Der Build läuft immer weiter. Der lokale hadolint-Befehl in CONTRIBUTING legt das Image per Digest fest
  (`hadolint/hadolint:v2.15.1@sha256:32dac941...`, FRESHNESS_PLAN F-5) und nennt alle fünf Dockerfiles, die die CI
  prüft.
- **Eine neuere Version eines Wissenspakets ließ die Auszüge der alten Version zurück** (DECISIONS Runde 23 Punkt 2):
  Paketseiten werden Stück für Stück ohne Ersetzen gespeichert, also behielt eine geänderte oder weggefallene Seite
  ihre alten Auszüge. Jeder Auszug trägt jetzt den Inhalts-Digest des installierten Pakets; sobald jede Seite der neuen
  Version drin ist, werden die Auszüge der älteren entfernt (`RAGManager.remove_pack_leftovers`), je Paket und
  Seitenquelle, nie die anderer Pakete oder deine eigenen Seiten; eine einsortierte lose Datei ersetzt auch die
  Auszüge, die eine frühere Version unsortiert aus ihr importiert hat. Der Snapshot vor dem Import bleibt. Eine Paketseite,
  die sich nicht importieren ließ, landete früher nur im Log; warum ein Paket wartet, wird jetzt nach der Installation
  gesagt (`last_sync_problem`): nur, warum das installierte Paket wartet (ein anderes wartendes Paket wird beim Start
  der Wissensbasis genannt), und der Community Hub bietet die Liste der Snapshots zum Löschen nur an, wenn der vorher
  genommene Snapshot nicht passte. Eine einsortierte lose Datei bekommt einen Namen, der in ihrem Ordner frei ist,
  bevor du gefragt wirst (auch wenn die Datei in einem Ordner dieses Themas lag), nie einen Windows-Gerätenamen, und
  ihr Einsortieren entfernt nie die Auszüge eines installierten Pakets gleichen Namens oder eines Target-Pakets. Ein
  Wissenspaket, dessen Ordner es schon gibt, wird vor der Frage abgelehnt, wie andere Pakete. Windows speichert kein
  Ausführungsrecht, also passte ein installiertes Paket mit Skripten von Linux oder macOS (jedes Target, das der
  Modul-Assistent macht) dort nie zu seinem Digest: Sein Wissens-Snapshot wäre nie importiert worden, und **Installierte
  Pakete prüfen** nannte es verändert. Die Herkunftsdatei hält jetzt auch den Digest des Pakets mit den Rechten, wie
  das Dateisystem sie behält (nur wo er abweicht; die Inhalte bleiben die des Manifests, also fällt eine später
  geänderte Datei weiter auf), und beide Prüfungen nehmen ihn an. Gecrawlte Seiten ersetzten ihre früheren Auszüge
  schon und werden direkt nach dem Download gespeichert (unverändert).
- **`ditto models download` ohne Stufe lädt das Modell, das ein einfaches `runtime start` lädt** (Kette
  port-runtime, aus der Prüfung des verwalteten llama-server): `runtime start` lädt die früher mit `--model` gewählte
  Stufe, der Download ohne Stufe holte aber die Empfehlung für diesen Computer; nach der Wahl einer anderen Stufe
  scheiterte der nächste Start daher weiter mit „not downloaded“. Der Download nimmt jetzt die früher gewählte Stufe
  („Chosen earlier with 'runtime start --model': ...“), sonst wie bisher die Empfehlung
  (`runtime/ditto_models.model_to_download`). **Ditto > Lokale Modelle...** in der GUI macht es genauso: Mit einer
  früher gewählten Stufe heißt sein Knopf **Gewähltes Modell herunterladen...** und fragt nach dieser Stufe. Doku:
  README, `docs/CLI_Usage_DE.md`, `docs/Features_DE.md` (EN/DE). Regressionstests: `tests/test_runtime.py`
  `test_model_download_defaults_to_the_model_runtime_start_loads`, `tests/test_ditto_models_core.py`
  `test_without_a_tier_the_download_is_the_model_runtime_start_loads`, `tests/test_gui_ditto_models.py`
  `test_without_a_selection_the_download_is_the_model_the_server_loads`.
- **Ein Rechner, dessen GPU keinen Vulkan-Treiber hat, bekommt den CPU-Build** (Kette port-runtime, aus der Prüfung des
  verwalteten llama-server): Lief `vulkaninfo`, listete aber nur einen Software-Renderer (llvmpipe, lavapipe), setzte
  die Hardware-Erkennung trotzdem `VULKAN=ON`, weil ein Vulkan-Loader, ein Grafikadapter (Windows) oder
  `vulkan-1.dll` da war; die Wahl des offiziellen vorgebauten Archivs und der Plan des selbst gebauten Servers nahmen
  daher den Vulkan-Build, der nur llvmpipe gefunden hätte. Ein solches `vulkaninfo` bedeutet jetzt `VULKAN=OFF`;
  Loader, Adapter und DLL zählen nur, wenn `vulkaninfo` fehlt oder scheitert. Gleich in allen drei Erkennungen:
  `orchestrator/runtime/hardware_probe.py` (mit seiner bytegleichen Kopie `scripts/hardware_probe.py`),
  `scripts/hardware_probe.sh` und `scripts/hardware_probe.ps1`. Regressionstests: `tests/test_runtime.py`
  `test_vulkaninfo_with_only_a_software_renderer_means_no_gpu_driver`,
  `test_windows_vulkaninfo_with_only_a_software_renderer`, `tests/test_hardware_probe.py`
  `test_a_software_renderer_only_means_no_vulkan` (das .sh und das .py auf demselben Rechner).
- **`runtime start --model` und `--sleep-idle` zählen auch, während ein Server läuft** (Kette port-runtime, aus der
  Prüfung des verwalteten llama-server): Ein Start, der den laufenden Server schon mit dem gewählten Modell vorfand,
  kehrte zurück, bevor die Wahl gespeichert war; `runtime start --model X` (oder `--model auto`, und die Modellwahl
  der GUI) bei laufendem Server ging daher verloren, und der nächste einfache Start lud wieder die Empfehlung. Die
  Wahl wird jetzt in beiden Fällen unter der Laufzeit-Sperre gespeichert (ein Evaluationslauf, `remember=False`,
  behält weiterhin die frühere). Eine ausdrückliche Leerlaufzeit (`--sleep-idle`, das Feld der GUI), die von der des
  laufenden Servers abwich, wurde übergangen; der Server speichert jetzt seine wirksame Leerlaufzeit und wird neu
  gestartet, wenn eine andere verlangt wird (ein Build ohne die Option läuft immer mit 0 und wird dafür nicht neu
  gestartet). Auf Bare Metal und im Docker-Modus. Regressionstests: `tests/test_runtime.py`
  `test_choosing_a_tier_is_remembered_when_the_running_server_is_reused`,
  `test_an_explicit_idle_time_restarts_a_server_started_with_another`,
  `test_an_idle_time_the_build_cannot_use_restarts_nothing`, `tests/test_runtime_mode.py`
  `test_a_reused_container_remembers_the_tier_and_another_idle_time_restarts_it`.
- **Die Sperre der lokalen Laufzeit wird nicht mehr minutenlang gehalten** (Kette port-runtime, aus der Prüfung des
  verwalteten llama-server): `ditto runtime install --prebuilt` (und **Offizielles fertiges Archiv
  installieren…** in der GUI) lud die Archive herunter, während es die Laufzeit-Sperre hielt; ein
  `runtime start` oder `runtime stop` anderswo wartete daher den ganzen Download ab (ein CUDA-Build hat Hunderte MB),
  bis zu den 15 Minuten der Sperre. Die Archive werden jetzt außerhalb der Sperre geladen (jede Datei hat ihre eigene);
  unter der Sperre prüft die Installation erneut, ob ein anderer Prozess denselben Build inzwischen installiert hat
  (dann wird er so genommen), ob ein Server daraus läuft, und lädt die Archive erneut, wenn eine gleichzeitige
  Installation sie entfernt hat; ein laufender Server wird weiterhin abgelehnt, bevor etwas heruntergeladen wird, und
  die Ordner, die eine abgebrochene Installation hinterließ, werden weiterhin (unter der Sperre) entfernt, bevor der
  Platz zum Entpacken geprüft wird (Review von port-runtime: die Prüfung war vor dieses Entfernen gerutscht). Der
  Exit-Handler eines an seinen Prozess gebundenen Servers (`start(tie_to_process=True)`) wartet höchstens 5 s auf die
  Sperre und beendet danach trotzdem genau den Prozess, den er gestartet hat (PID, Startzeit und Programm geprüft),
  statt den endenden Prozess hinter einem anderen Start am Leben zu halten. Regressionstests:
  `tests/test_runtime.py` `test_downloads_do_not_hold_the_runtime_lock`,
  `test_an_install_finished_meanwhile_by_another_process`,
  `test_the_leftovers_of_an_interrupted_install_are_removed_before_the_space_check`,
  `test_exit_handler_does_not_wait_for_a_busy_lock`.
- **Ein gepinnter Download, der nicht gelingen kann, sagt sofort, warum** (Kette port-runtime, aus der Prüfung des
  verwalteten llama-server; `orchestrator/utils/download.py` `PinnedDownloader`, den die Builds und Modelle der lokalen
  Laufzeit und der Community-Zoo teilen): Ein TLS-Zertifikat, das sich nicht prüfen ließ, wurde wie eine abgebrochene
  Verbindung fünfmal mit Wartezeit erneut versucht und endete mit „did not complete after 5 attempts“; jetzt endet es
  sofort mit „the TLS certificate of <host> could not be verified“ (nur der Host, nie die möglicherweise signierte
  Download-URL; nach einer Weiterleitung der Host, der scheiterte, etwa ein Release-CDN, nicht der erste, Review von
  port-runtime; hinter einem Proxy, der TLS prüft, gehört sein CA-Zertifikat in `REQUESTS_CA_BUNDLE`). Eine Verbindung,
  die während ihres TLS-Handshakes endet (ein wackelnder Link, ein Proxy) oder deren TLS-Record unterwegs bricht, ist
  kein Zertifikatsproblem: Sie wird wie jede abgebrochene Verbindung erneut versucht und fortgesetzt (Review von Stufe
  C, Welle 3). Jeder andere Anfragefehler (zu viele Weiterleitungen, eine ungültige URL) und eine `.part`-Datei, die
  sich nicht schreiben lässt (volle Platte, keine Berechtigung), kamen als rohe Ausnahme heraus; jetzt enden sie mit
  einer klaren Meldung, und der Teil bleibt für die Fortsetzung erhalten. Der Hugging-Face-Fetcher (`hf_hub.HubFetcher`:
  die Downloads des Browsers, des Builders und einer KI) lief mit einer eigenen Kopie des Bereichs-Downloads ohne diese
  Korrekturen; jetzt läuft er über denselben und ergänzt nur, was zum Hub gehört (das Token nur für huggingface.co, die
  Ablehnung eines anderen Hosts und von HTTP 401 und 403), deshalb wird auch dort eine Größe auf dem Server, die nicht
  der vom Hub gelisteten entspricht, abgelehnt, bevor etwas geschrieben wird (Stufe C, Welle 3). Regressionstests:
  `tests/test_runtime.py` `TestDownloads`, `tests/test_hf_tools.py` `TestTheFetcherSaysWhyAtOnce`.
- **Die lokale Laufzeit scheitert unter Windows nicht mehr, während ein anderes Fenster ihre Dateien liest** (Kette
  port-runtime, aus der Prüfung des verwalteten llama-server): Windows verweigert das Ersetzen oder Löschen einer
  Datei, die ein anderer Prozess gerade offen hat. Der LLMClient der GUI liest `server.json` und die Schlüsseldatei
  des Servers, und ein Prozess, der auf die Laufzeit-Sperre wartet, liest die Sperrdatei; ein Start, Stopp oder eine
  Installation in einem anderen Fenster konnte daher mit „The process cannot access the file“ enden. Die
  Zustandsdateien der Laufzeit (auch die des Build-Speichers und des Laufzeitmodus) werden jetzt mit
  `utils/atomic.replace_file` ersetzt, und `server.json`, die Schlüsseldateien und die Sperrdateien von
  `utils/download.FileLock` (die Laufzeit-Sperre, die des Build-Speichers und die einzelner Downloads) mit dem neuen
  `utils/atomic.remove_file` gelöscht; beide versuchen es unter Windows kurz erneut, sonst nie. Regressionstests:
  `tests/test_atomic.py`, `tests/test_runtime.py` `test_windows_sharing_violations_are_retried`.
- **Dittos Tool-Aufrufe und ihre Ergebnisse passen immer zusammen** (Übernahme aus dem behaltenen Worktree
  ditto-rewire-verified): Jeder Anbieter lehnt ein Gespräch ab, das nach einem unbeantworteten Tool-Aufruf
  weitergeht, also scheiterte jede spätere Frage auf so einem Verlauf. Eine an der Längengrenze abgeschnittene
  Antwort ließ einen Tool-Aufruf unbeantwortet; die Tool-Schleife beantwortet ihn jetzt mit einem Fehlerergebnis
  (der Aufruf läuft nie), und ein Verlauf, den eine frühere Version mit einem offenen Aufruf am Ende gespeichert hat,
  wird vor der nächsten Frage durch angehängte Ergebnisse geschlossen (frühere Runden bleiben unverändert). Ein
  gespeichertes Gespräch, dessen Aufrufe und Ergebnisse nicht zusammenpassen (ein OpenAI-kompatibler Server kann so
  eines angenommen haben), beginnt neu mit dem Hinweis, dass es nicht gelesen werden konnte, und ein Wert
  `transcript` oder `messages`, der keine Liste ist, bricht das Laden des Chats nicht mehr ab. Eine Umsetzung in
  `orchestrator/llm/tool_loop.py` (`CALL_NOT_RUN`, `open_tool_calls`, `close_open_calls`,
  `conversation_is_consistent`). Regressionstests: `tests/test_ditto.py` `TestToolCallsAndResultsPairUp`.
- **Eine gescheiterte Ditto-Runde, in der schon Tools liefen, sagt das**: Scheitert der Anbieter oder lehnt das
  Modell ab, nachdem in der Runde schon Tools liefen, nennt die Meldung sie und sagt, dass Ditto sich die Runde
  nicht merkt, damit du prüfst, was sie getan haben (vielleicht wurde ein Build gestartet), bevor du noch einmal
  fragst. Eine Ablehnung in der Tool-Schleife nimmt denselben Weg. Regressionstest: `tests/test_ditto.py`
  `test_a_failed_turn_after_tools_ran_names_them_and_says_it_is_not_remembered`.
- **Ein Gespräch mit Tool-Runden, das ohne Tools gefragt wird, geht als neues weiter**: **Ohne Werkzeuge
  antworten** im Chat-Bereich schickte die früheren Tool-Aufrufe und Ergebnisse ohne Tool-Definitionen, was Claude
  ablehnt. So ein Gespräch wird jetzt wie ein langes zu einem neuen zusammengefasst, und der Hinweis sagt, warum
  (`ChatReply.new_conversation_reason`, `chat.notice.new_conversation_no_tools`). Regressionstests:
  `tests/test_ditto.py` `test_a_conversation_with_tool_turns_asked_without_tools_continues_as_a_new_one`,
  `tests/test_chat_panel.py` `test_the_new_conversation_notice_says_why`.
- **Strg+C an Dittos Bestätigung im Terminal ist ein Nein**: `llm-cli ditto ask` stürzte ohne stdin ab, und Strg+C
  oder das Ende der Eingabe an der Bestätigung oder an der y/a/n-Rückfrage einer dauerhaften Freigabe kam beim Modell
  als gescheiterter Tool-Aufruf an („Abort“), ohne Zeile `not_confirmed` im Audit-Log. Beides ist jetzt ein
  protokolliertes Nein; `module generate-ai` meldet eine Probe, die sich nicht lesen lässt (ein Ordner, keine
  Berechtigung), oder ein Modul, das sich nicht speichern lässt, statt eines Tracebacks. Regressionstests:
  `tests/test_ditto.py` `TestConfirmations`.
- **Der Offline-Modus lehnt beim Speichern einen eigenen Server außerhalb dieses Computers und des lokalen Netzes
  ab**: Die KI-Einstellungen (Dialog, Anmeldung, MCP `update_settings`) lehnten im Offline-Modus nur Cloud-Vorlagen
  ab, also wurde ein eigener Server mit öffentlicher Adresse gespeichert und erst abgelehnt, wenn Ditto gefragt
  wurde. `AISettingsChange.problems()` nutzt jetzt die Regel der LLM-Schicht selbst (`preset.is_local(address)`).
  Regressionstests: `tests/test_ai_settings_change.py` und `tests/test_dialogs.py`
  `test_offline_mode_accepts_a_custom_server_only_on_the_local_network`.
- **Im Offline-Modus nimmt der Hugging-Face-Browser keinen Kontakt mehr zu Hugging Face auf** (Konsolidierungskette
  hf-online): Suche, Lizenzprüfung, Dateiliste und Download gingen bei eingeschaltetem Offline-Modus online, während
  die Aufrufe einer KI abgelehnt wurden. **Tools > Hugging Face Model Browser...** lehnt jetzt vor jeder Anfrage ab,
  bevor ein Worker startet oder nach einem Token gefragt wird, in klaren Worten in deiner Sprache: Der Offline-Modus
  ist an, es wird nichts gesucht, geprüft oder geladen, dann die Worte, die dir jede andere Ablehnung im
  Offline-Modus zeigt: den Offline-Modus ausschalten (Ditto > KI-Einstellungen...) oder ein Modell nehmen, das schon
  auf diesem Computer ist. `ModelManager` lehnt für jeden Aufrufer gleich ab
  (`hf_hub.offline_refusal`, dieselbe Ablehnung, die `HubClient` einer KI gibt; `hf_hub.person_text` gibt einem
  Fenster oder der Kommandozeile die Worte für die Person). **Alte Downloads übernehmen…** funktioniert offline: Eine
  Datei, deren Prüfsumme nur Hugging Face liefern könnte (ein Cache ohne Links), bleibt mit dem Grund „der
  Offline-Modus ist an“ im alten Cache, und Hugging Face wird nicht gefragt; `llm-cli models take-over` verhält sich
  genauso. `download_hf_file` einer KI prüft nach deiner Antwort erneut (Review von Stufe C, Welle 3): Wurde der
  Offline-Modus eingeschaltet, während die Frage offen war, lehnt der Download mit derselben Ablehnung ab, bevor etwas
  reserviert oder angefragt wird. Regressionstests: `tests/test_hf_online.py` `TestOfflineMode`,
  `TestTheBrowserWindowOffline`, `tests/test_hf_tools.py`
  `test_offline_mode_switched_on_while_the_person_is_asked_refuses_before_any_request`.
- **Ein Modell aus dem alten Download-Cache von Hugging Face funktioniert wieder im Build** (A6, „bleiben nutzbar“):
  Der Hugging-Face-Browser der GUI speicherte bisher in den eigenen Cache von huggingface_hub im Modellordner, wo eine
  Datei ein Link auf einen Blob ist, der nach seiner Prüfsumme heißt. Ein Build einer solchen GGUF-Datei band sie
  unter diesem Prüfsummen-Namen ein, sodass der Build keine `.gguf`-Datei mehr sah (`config_module.sh` erkennt eine
  GGUF-Eingabe an ihrer Endung) und sie umzuwandeln versuchte; ein Modellordner aus dem Cache, im Builder über seinen
  Pfad gewählt, wurde als Ganzes eingebunden, und seine Links auf die Blobs außerhalb lösten sich im Container nicht
  auf. Ein Link auf einen Cache-Blob (eine Datei, die nach ihrer Prüfsumme heißt) behält jetzt im Build den Namen
  seines Links (`tools/local_files.py` `mount_plan` und der lokale Pfad des Builders), und ein eigener Ordner, dessen
  Links auf Cache-Blobs führen, wird Datei für Datei eingebunden, wie es die Referenz eines KI-Aufrufers schon wurde
  (`file_binds`, `linked_folder_binds`). Jeder andere Link und ein Ordner mit einem anderen Link (oder einem, den
  das Einbinden ablehnt: kaputt, auf einen Ordner, ein zweiter Name für eine Datei) werden genau wie bisher
  eingebunden: Der eigene Name eines Links würde den Modellnamen des Builds ändern, und ein Link aus einem eigenen
  Ordner hinaus wird nie in den Container verfolgt. Regressionstests: `tests/test_hf_gui_download.py`
  `TestTheOldCacheIsUsableForABuild`.
- **Ein Problem auf diesem Computer heißt nicht mehr „das Board antwortet nicht“**: Jeder Kontakt mit einem Board
  zählte einen Fehler, der keiner der vier Grundcodes war, als `device_host_down`. Eine volle oder schreibgeschützte
  Festplatte oder fehlende Rechte an den eigenen Dateien des Frameworks (sein SSH-Schlüssel, known_hosts) sagten der
  Person deshalb, sie solle das Board einschalten, das Deployment wartete umsonst, und das Board wurde für Mojo als
  nicht erreichbar vermerkt. Ein solcher Fehler hat jetzt einen eigenen Grund, `local_problem` („Etwas auf diesem
  Computer hat die Verbindung verhindert, nicht das Board“), mit dem nächsten Schritt auf Deutsch und Englisch
  (Speicherplatz freigeben, den Statusordner und die Sicherheitsprogramme dieses Computers prüfen), dem nächsten
  Schritt für die KI und Mojos Prüfung der Installation als Schritt aus dem Katalog (`orchestrator/remote/reasons.py`
  `is_local`, `LOCAL_PROBLEM`, `CODES`). Er wird nie beim Board vermerkt. Der Grund des Deployments nennt die Datei
  ohne ihren Ordner, und einen Home-Ordner oder einen Ordner mit dem Kontonamen dieses Computers nur nach seiner Art,
  denn dieser Name ist der Name eines Kontos und der Grund erreicht eine KI (die Namen des Kontos sind USER, USERNAME
  und der Name jedes Home-Ordners, den `orchestrator/security/paths.py` `home_folders` kennt, auch USERPROFILE); die
  CLI zeigt zusätzlich den Fehler selbst. Er ist keine Sperre der Delegationsregeln, deshalb
  veraltet keine Delegation.
- **Ein Deployment klappt auch auf einem Board, dessen Konto eine andere Login-Shell nutzt**: sshd gibt jeden Befehl
  an die Login-Shell des Kontos auf dem Board. Die Befehle des Deployments (der private Ordner, die Hash-Prüfungen, der
  Start der Installation, die auf dem Board selbständig läuft, und das Abfragen ihres Endes) und die Installation des
  Framework-Schlüssels gingen so dorthin, wie sie waren; bei einem Konto mit fish, csh oder einer anderen Shell, die
  nicht POSIX ist, bedeuteten ihre `&&`, `||`, `2>/dev/null` und `$?` etwas anderes oder nichts. Jeder davon kommt jetzt
  als ein `sh -c '...'` auf dem Board an (`orchestrator/remote/transport.py` `sh_command`, die Form, die die
  Geräte-Laufzeit und die Board-Prüfung schon nutzten), sodass das sh des Boards das Skript liest, egal welche
  Login-Shell das Konto hat.
- **Die `known_hosts` des Frameworks wird wie jede andere private Datei geschrieben**: Das Vertrauen in den
  Host-Schlüssel eines Boards (und die Änderung von Adresse oder Schlüssel eines Boards) schrieb die Datei über eine
  temporäre Datei, die liegen blieb, wenn das Schreiben scheiterte (zum Beispiel bei voller Platte), und unter Windows
  scheiterte das Vertrauen, wenn ein anderes Programm die Datei gerade offen hatte. Die temporäre Datei verschwindet
  jetzt wieder, und das Ersetzen ist das aus `orchestrator/utils/atomic.py`, das es unter Windows kurz erneut versucht
  (`orchestrator/utils/ssh_trust.py` `save_host_keys`).
- **Ein Deployment wartet weiter, während der SSH-Server des Boards neu startet**: Nach einem Netzabbruch wartet das
  Deployment auf das Board (N5). Antwortete das Board wieder, während sein SSH-Server noch startete, öffnete sich die
  Verbindung und brach wieder ab (ein Reset, ein Abbruch, eine unterbrochene Pipe oder EOF beim Schlüsselaustausch);
  keiner dieser Fälle hatte einen Grundcode, deshalb gab das Deployment sofort auf, statt weiter zu warten. Während es
  wartet, heißt ein solcher Abbruch jetzt „noch nicht zurück“, und das Warten geht weiter
  (`orchestrator/remote/reasons.py`, `reach_code(..., while_waiting=True)`). Der erste Kontakt des Deployments nutzt
  außerdem die Regel aller anderen Wege: Ein Verbindungsfehler, der keiner der vier Codes ist, heißt, dass das Board
  nicht antwortet (`device_host_down`, für Mojo festgehalten), statt eines leeren Codes.
- **Die beim ersten Start gewählte Sprache gilt sofort**: Der Dialog beim ersten Start lief mit englischen Texten des
  Fensters, und die Wahl Deutsch ließ sie bis zum nächsten Start englisch (auch Qts eigene Schaltflächen). Das Fenster
  wechselt jetzt in die gewählte Sprache, bevor es sich öffnet (`orchestrator/gui/app.py`).
- **Dependabot schlägt für die GPU-Vorlage kein anderes Ubuntu-Release mehr vor**:
  `targets/_template/Dockerfile.gpu` baut auf dem Ubuntu-Release auf, auf das die CUDA-Redist-Archive und der
  Katalog der Build-Basen festgelegt sind (Entscheidung D6), deshalb brach ein Sprung auf 25.10 die Tests.
  `.github/dependabot.yml` ignoriert Wechsel des Ubuntu-Releases (major und minor); ein Test hält die Regel fest.
- **Die nächtliche Retrieval-Evaluation läuft wieder**: Der Workflow `.github/workflows/retrieval-eval.yml` startete
  das Framework auf einem GitHub-Runner, der keinen System-Keyring hat, ohne anzugeben, wie der Schlüsseltresor
  gehalten wird; deshalb verweigerte das Framework jede Nacht den Start (Exit-Code 5). Der Job setzt jetzt
  `LLM_SECRET_STORAGE=ephemeral` (nur im Arbeitsspeicher; die Evaluation braucht keine Zugangsdaten), und ein Test
  schlägt fehl, wenn ein Workflow-Schritt das Framework ohne diese Angabe startet.
- **Auf ein Board, dessen Home-Ordner einen langen Pfad hat, lässt sich deployen**: Die Geräte-Laufzeit lehnte einen
  Home-Ordner des Board-Benutzers mit mehr als 200 Zeichen ab (und dessen Staging-Ordner sowie Modelle darunter ab
  400), deshalb brach ein Home auf einem tief liegenden Einhängepunkt das Deployment mit „not a plain absolute path“
  ab. Die Zeichen werden wie bisher geprüft; die Länge ist jetzt nur noch eine Plausibilitätsgrenze weit unter der
  Grenze des Systems (1024 Zeichen für einen Ordner, 1536 für eine Datei darin, an einer Stelle in
  `runtime/device/launcher.py`). Die Gerätetests laufen auch unter einem langen temporären Ordner (wie auf dem
  Windows-Runner).
- **Ein beschädigter Speicher der festgehaltenen Autorenschlüssel wird einmal kopiert, nicht zweimal**: Eine
  Installation liest den Speicher zweimal (für ihre Zusammenfassung, dann zum Festhalten des Schlüssels), und die
  zweite Kopie derselben Beschädigung entstand noch einmal, wenn dieses Lesen in die nächste Sekunde fiel, weil die
  Kopie nur nach der Uhrzeit benannt war. Eine gleiche Kopie wird jetzt einmal aufbewahrt, und eine andere
  Beschädigung in derselben Sekunde bekommt einen eigenen Namen, statt eine aufbewahrte Kopie zu ersetzen.
- **Das Widerrufen eines MCP-Tokens in der GUI entfernt auch seine dauerhaften Freigaben, und der neue eigene
  Abschnitt eines Aufrufers behält die Tools, die nur im Framework gefragt werden**: Widerrufen in der GUI löschte das
  Token, ließ aber seine dauerhaften Freigaben (die von `mcp:NAME`) in der Berechtigungsdatei stehen, aufgelistet, bis
  man sie einzeln widerrief (sie galten nicht mehr); das Token, seine Berechtigungen und seine dauerhaften Freigaben
  gehen jetzt in einer Kernfunktion, die sich GUI und `llm-cli mcp token revoke` teilen. `llm-cli tools permissions
  --caller ditto|mcp` legte den neuen Abschnitt eines Aufrufers ohne die gemeinsame Liste der Tools an, deren Fragen
  dir nur im Framework gestellt werden, sodass diese Fragen für diesen Aufrufer wieder im eigenen Dialog des
  KI-Clients landen konnten; der neue Abschnitt beginnt jetzt auch mit dieser Liste, wie bei `--confirm-in-framework`.
- **Die Liste der Tools, die nur im Framework gefragt werden, geht nicht mehr durch einen zweiten Aufruf, eine Kopie
  oder die ersten eigenen Berechtigungen eines Tokens verloren**: `llm-cli tools permissions --caller ditto|mcp` für
  einen Aufrufer, der schon eigene Berechtigungen hatte, baute dessen Abschnitt ohne diese Liste neu; ein Token, das
  als Kopie der Berechtigungen Dittos oder der lokalen MCP-Clients entstand (`llm-cli mcp token add --policy-from`
  oder Hinzufügen... in der GUI), bekam keine; und ein neues Token, das der gemeinsamen Liste folgt, verlor sie bei
  seinem ersten `tools permissions --token NAME` (oder Berechtigungen... in der GUI), das mit einer leeren Liste
  begann. Jedes Mal konnten die Fragen zu diesen Tools wieder im eigenen Dialog des KI-Clients landen. Die
  Berechtigungen, die für einen Aufrufer gezeigt und kopiert werden, tragen jetzt die Liste, die für ihn gilt
  (`ToolRegistry.policy`).
- **Die Token-Liste spricht die Sprache des Fensters, und „Server starten“ sagt, warum es nicht geht**: der Abschnitt
  „MCP-Tokens (eines je Client)“ in Security > Verbindungen zu KI-Apps (MCP)... und in Tools > Tool Permissions (Ditto
  / MCP)... war nur englisch (Überschrift, Hinweis, Spalten, Schaltflächen, die Fragen beim Hinzufügen und Rotieren);
  er ist jetzt englisch und deutsch, und Rotieren sowie die Frage zu einem verknüpften Token kommen als reiner Text mit
  „Nein“ als Vorwahl. Ein Installationspfad, den PYTHONPATH nicht aufnehmen kann, ließ „Server starten“ ohne jede
  Meldung scheitern; jetzt sagt das Fenster den Grund, und nichts wird gestartet.
- **Community Hub und Speicher der Wissensbasis: Installation im Hintergrund, die Frage vor dem Pull Request, ein
  Qdrant-Server** (Prüfung von W1'): Ein Paket zu installieren (Install, Import file...) schob es nach deinem „Ja“
  im GUI-Thread an seinen Platz, was bei einem Wissenspaket einen Snapshot der Wissensbasis und den Import jeder Seite
  bei eingefrorenem Fenster bedeutete; das läuft jetzt im Hintergrund, und die Statuszeile nennt den zuvor
  angelegten Snapshot, wie `llm-cli zoo install`. **Open pull request** im Export-Fenster übergab das Paket sofort an
  einen öffentlichen Pull Request; es stellt jetzt zuerst die Frage von `llm-cli zoo export --pr` („Dieses Paket beim
  öffentlichen Community-Repository einreichen?“, „Nein“ ist die Vorgabe). Mit einem eingestellten Qdrant-Server
  (`qdrant_url`) nannten `llm-cli knowledge storage` und **Tools > Knowledge Snapshots...** den lokalen Speicher und
  boten an, ihn umzuwandeln, was den Server öffnete und nichts umwandelte; beide nennen jetzt den Server, und die
  Umwandlung wird abgelehnt, solange er eingestellt ist. Eine in **Import file...** oder im Export-Fenster eingegebene
  Passphrase bleibt nach Abbrechen oder Schließen nicht mehr im verborgenen Fenster.
- **Ein Build ersetzt keinen früheren Build mit demselben Namen mehr**: in 3.0.0 verschob ein abgeschlossener Build
  seine Dateien in den Ordner `<Target>/<Modell>/<Quantisierung>` und ersetzte dort die Dateien eines früheren Builds
  (zum Beispiel eines für ein anderes Board), so dass ein fertiges Golden-Artefakt ohne Frage verloren ging. Ein
  späterer Build kommt jetzt daneben (`Q4_K_M-2`, mit eigenem ZIP); der frühere Build bleibt ganz.
- **Das „Docker-Aufräumen“ der Selbstheilung löscht nichts mehr** (veröffentlicht in 3.0.0; Runde 5 f): Wer den
  Vorschlag für eine volle Docker-Platte freigab (`docker_prune`, im GUI-Dialog, an der CLI-Abfrage oder über
  `resolve_healing`), ließ die verwaisten Images des Frameworks löschen, ohne dass sie gezeigt wurden, und den ganzen
  Build-Cache des Docker-Daemons leeren, auch den anderer Projekte. Jetzt ist es ein manueller Schritt: Der Vorschlag
  verweist auf die Liste der Build-Images des Frameworks (**Free Docker space...** im Dialog, **Tools > Framework Build
  Images...**, `llm-cli doctor --fix --tier 2`), die nur die Images löscht, die du anhakst und bestätigst, und eine
  Freigabe wird abgelehnt, bevor etwas gefragt wird. Kein Code des Frameworks
  leert den Docker-Build-Cache mehr.
- **`llm-cli doctor --fix --tier 2` löscht nicht mehr ohne Liste** (veröffentlicht in 3.0.0; Runde 5 f): es leerte den
  ganzen Build-Cache des Docker-Daemons, auch den anderer Projekte, und löschte die verwaisten Images des Frameworks,
  ohne sie zu zeigen. Der Build-Cache wird nicht mehr angerührt (er lässt sich nicht auf das Framework beschränken).
  Stufe 2 listet jetzt jedes Build-Image des Frameworks (ohne Namen, benannt `llm-framework/<Target>:last-build`,
  Builder der Laufzeit), jedes mit Namen, Image-ID, Größe und was es ist, nichts ausgewählt, und löscht genau die
  ausgewählten, per Image-ID oder Name und ohne Prune, erst nach deinem Ja im Terminal (die Vorgabe ist Nein).
  Ohne Terminal und mit `--json` listet es sie nur; das Löschen wird zuerst auditiert und abgelehnt, wenn sich die
  Liste inzwischen geändert hat, und ein Image, das inzwischen einen Namen bekommen hat (ein Build eines anderen
  Framework-Programms), bleibt erhalten. Kein Tool, keine Ressource und kein Prompt erreicht es, eine KI-App oder
  Ditto kann es also weder ausführen noch unter einer Vollmacht bekommen. Die Befunde zum Speicherplatz sagen, was
  Stufe 2 listet, statt ein Aufräumen zu versprechen.
- **Eine Reparatur wird genannt, wie auch immer der Aufruf endet** (Befund eines echten Laufs mit Claude Code 2.1.296
  über stdio, Protokoll 2026-07-28): ein erklärter Alias, den der Aufruf benutzte (`quant` gelesen als
  `quantization`), wurde im Audit-Log festgehalten, aber ein Aufruf, der vor oder statt der Frage abgelehnt wurde (zu
  wenig Speicherplatz laut Vorabprüfung, ein Limit, ein ungültiger Wert, eine verweigerte Berechtigung, ein „Nein“,
  eine Frage, die niemand rechtzeitig beantwortete, die Wiederholungssperre), sagte dem Client nichts davon, sodass
  das Modell den richtigen Argumentnamen nie lernte. Jede Ablehnung und jeder Fehler nach einer Reparatur trägt jetzt
  dieselben Hinweise wie ein erfolgreiches Ergebnis: am Ende des Textes („Notes: ...“) und über MCP im strukturierten
  Fehler (`{"error": {"code", "field", "notes"}}`), und auf Dittos Weg im Prozess. Eine
  Ablehnung durch den Normalisierer nach einem Alias (ein ungültiger Wert, ein unbekanntes Argument daneben) hält den
  Alias ebenfalls im Audit-Log fest. Ein registerweiter Test schickt jedes Tool mit erklärten Aliasen durch jede Art
  von Ablehnung auf beiden Wegen. Die Reparatur, die die Vorabprüfung eines Tools macht (`download_hf_file` mit
  einem Dateinamen in anderer Groß- und Kleinschreibung), ist eine Reparatur wie die anderen: über MCP ging ihr
  Hinweis sogar in einem erfolgreichen Ergebnis verloren (die Vorabprüfung läuft vor der Frage mit einem eigenen
  Kontext), eine Ablehnung danach (zu wenig Speicherplatz) nannte sie nie, und das Audit-Log hielt sie nicht fest;
  jetzt wird sie im Ergebnis und in jeder Ablehnung danach auf jedem Weg genannt und nur mit Art und Feld
  festgehalten. Der Job einer wartenden Freigabe, der abgelehnt oder verweigert endet, nennt die Reparaturen des
  Aufrufs in `get_job_status` (`error.notes`), wie es die Hinweise seines Ergebnisses tun.
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
- Unter Windows landet der Build eines lokalen Modells wieder im Ausgabeordner (veröffentlicht in 3.0.0): Der
  Build-Ordner hieß nach dem Teil des Modells hinter dem letzten `/`, und das ist bei einem Windows-Pfad wie
  `C:\models\my-model` der ganze Pfad, also ging der Build in den Ordner des Modells selbst
  (`C:\models\my-model\<quantization>`), und keine Liste fand ihn. Der Ordner heißt jetzt auf jedem System nach
  dem letzten Teil an `/` oder `\`.
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
  Dateien 0600). Ein Eintrag nennt das Ergebnis des Builds, bevor sein Build-Platz frei wird: Wer sieht, dass der
  Build zu Ende ist, liest dort das Ergebnis, nie die Stufe davor.
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
- Windows: Der Uninstaller (`scripts/Uninstall-LLM-Cross-Compiler-Framework.bat`) findet die Installation.
  `install.bat` schreibt jetzt die Markierungsdatei mit dem Installationsordner, die der Uninstaller liest, neben
  `install.bat`, wo der Uninstaller sie sucht; vorher schrieb kein Installer sie, und der Uninstaller meldete immer,
  es sei nichts installiert. Meldet PowerShell keinen Skriptpfad (der PowerShell-Teil von `install.bat` läuft über
  `Invoke-Expression`), nimmt `install.bat` den aktuellen Ordner, den sein Batch-Teil auf den eigenen gesetzt hat.
  Das Startskript
  `scripts/start-llm_cross_compiler_framework.bat` funktioniert auch aus `scripts\` (es suchte `orchestrator\main.py`
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
- Windows: Ein Zielmodul aus dem Modul-Assistenten, `llm-cli module create` oder `create_target_module`
  (veröffentlicht in 3.0.0) hatte Windows-Zeilenenden (CRLF) in seinem `modules/build.sh`, seinen anderen Skripten,
  seinen Dockerfiles und seiner `target.yml`, sodass bash im Linux-Build-Container schon an der ersten Zeile abbrach
  (`$'\r': command not found`). Seine Skripte, Dockerfiles und `target.yml` werden jetzt auf jedem Rechner mit LF
  geschrieben. Auch ein Windows-Checkout des Repositorys behält in jeder Textdatei LF (`.gitattributes`: die
  Dockerfiles der Build-Container, Python-Skripte, LICENSE-Dateien und Git-Hooks waren nicht erfasst), außer in den
  nur unter Windows laufenden `.bat`- und `.ps1`-Skripten.
- Windows: Der Kalibriertext für die Importance-Matrix (veröffentlicht in 3.0.0) wurde mit Windows-Zeilenenden
  geschrieben, sodass `llama-imatrix` sie aus anderem Text berechnete als unter Linux und macOS; er wird jetzt auf
  jedem Rechner mit LF geschrieben.
- `install.sh` prüft die Verbindung per HTTPS zum Paketindex (`pypi.org` oder `PIP_INDEX_URL`) statt mit einem Ping
  an 8.8.8.8, den viele Netze sperren. Er lädt dazu eine kleine Projektseite statt des ganzen Index, zeigt
  Zugangsdaten in `PIP_INDEX_URL` nicht an und bricht mit einer klaren Meldung ab, wenn er mit `sh` statt bash
  gestartet wird. Er legt keinen ungenutzten Datenordner im Home-Ordner von root mehr an. Ein erneuter Aufruf
  entfernt nur Dateien, die ein früherer Aufruf kopiert hat (aufgeführt in `.lcf-installed-files`) und die das
  Repository nicht mehr ausliefert; vorher löschte sein `rsync --delete` alles in `/opt`, was das Repository nicht
  ausliefert: mit dem Modul-Assistenten oder `llm-cli module create` erstellte Targets, installierte Community-Packs
  sowie Builds, Cache, Modelle und `configs/config.yml`. Lässt sich die Liste nicht erstellen, bricht er ab, bevor
  etwas kopiert oder entfernt wird; ein veralteter Link wird selbst entfernt, nie die Datei, auf die er zeigt; und
  die Kopie von `install.sh` in `/opt` kopiert und entfernt nichts. Aus einem Ordner ohne Versionsverwaltung (ein
  entpacktes Archiv) installiert, überspringt er nur die Ordner `build/` und `dist/` der obersten Ebene, nicht mehr
  jeden Ordner namens `build` (darunter das frameworkeigene `orchestrator/runtime/build/`). Lässt sich die
  Python-Umgebung nicht einrichten (kein Modul `venv`, ein festgelegtes Paket, das der Index nicht anbietet), bricht
  er mit einer Meldung ab; vorher meldete er die Installation mit leerer Umgebung als abgeschlossen.
- `make build`, `make up`, `make gui`, `make down` und `Docker Setup/docker-compose.yml` funktionieren: Die
  Compose-Datei baute aus einer `orchestrator/Dockerfile`, die es nie gab, und das Makefile rief damit das alte
  `docker-compose` und das fehlende `scripts/setup_linux.sh` auf. Das Makefile nutzt `docker compose` (v2) und den
  Builder; `make setup` und `make test-container` sind weg (sie brauchten die fehlenden Dateien; Tests laufen mit
  `make test`), ebenso die Compose-Dienste mit nicht festgelegten Fremd-Images und dem Docker-Socket (Socket-Proxy,
  ctop, Trivy). `make audit` prüft ein Archiv des Images mit der festgelegten Trivy-Version, ohne den Docker-Socket.
  `make clean` löscht das Datenvolume, `output/` und `cache/` nicht mehr ohne Nachfrage: Es entfernt nur Container,
  die außerhalb des Volumes nichts von dir enthalten, und zeigt, was Daten enthält.
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
  nachgeschlagen; hat sich ein Fakt geändert, von dem die Entscheidung abhängt (etwa ein neuer Upstream-Commit; freier
  Speicher allein zählt nie), wirst du erneut gefragt, sonst läuft der Aufruf gebunden an die Fakten, die du gesehen
  hast. Eine MCP-Rückfrage zwischen 4.000 und 16.000 Zeichen
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
- **Ein Sandbox-Benchmark eines Modells in einem schreibgeschützten Ordner scheiterte mit einem allgemeinen Fehler**:
  `run_benchmark` mit `sandbox=true` legte seinen privaten Link des geprüften Modells nur neben dem Modell an. Jetzt
  weicht es auf einen privaten Ordner im eigenen Cache- oder Zustandsordner des Frameworks auf demselben Dateisystem
  aus, sonst auf eine Kopie, die aus der geprüften offenen Datei gelesen und erneut gehasht wird (ihr sha256 muss der
  geprüfte sein); nur wenn nichts davon geht, lehnt es ab, mit dem Grund (`insufficient_resources` oder
  `needs_person`). Der private Ordner wird entfernt, wenn der Lauf endet, auch bei einem Fehler und bei `cancel_job`,
  das jetzt auch den Sandbox-Container beendet, eine noch laufende Kopie anhält und nach dem Abbruch keinen Container
  mehr startet; einen Ordner, den ein Absturz zurückließ, entfernt ein späterer Lauf.
- **Job-Enden im Audit-Log**: Wenn das Eintragen eines fertigen Deployments in die Geräteliste scheiterte, schrieb
  `deploy_to_device` keinen End-Eintrag, obwohl das Deployment stattgefunden hatte; der End-Eintrag wird jetzt auf
  jedem Weg aus dem Job geschrieben (das Ergebnis sagt dann, dass die Geräteliste es nicht eingetragen hat).
  `create_deploy_package`, die Board-Laufzeit-Jobs (`probe_remote_device`, `deploy_device_runtime`,
  `run_device_benchmark`) und die lokalen Laufzeit-Jobs (`build_local_runtime`, `start_local_runtime`,
  `run_benchmark`) schreiben jetzt ebenfalls Job-End-Einträge (ok, Fehler mit seinem Grundcode oder abgebrochen),
  verknüpft mit dem Start-Eintrag des Aufrufs und mit Geräten und Modellen unter ihren Bezeichnungen. Die Deploy- und
  Laufzeit-Tools erreichen gebaute Modelle über die öffentliche Methode `BuildService.artifact_entry` statt über eine
  private.
- **Der Katalog der Upstream-Projekte ist stimmig** (`configs/detailed_project_sources.yml` und
  `configs/project_sources.yml`, ausgeliefert in 3.0.0): Jeder Größenfaktor hat eine Bezugsgröße (FP16 = 1.0x,
  berechnet aus den Bits pro Gewicht) statt FP32 für manche Formate und Q8_0 für andere, und die Beispielgrößen folgen
  daraus; die Tabelle der Tokens pro Sekunde und die Beschleunigungsfaktoren ohne Quelle sind entfernt; der Hinweis zu
  Hailo nennt „nicht für LLM-Inferenz“ nicht mehr als Tatsache, sondern sagt, die aktuelle Dokumentation von Hailo zu
  prüfen; der alte TensorRT-LLM-Stand v0.10.0 ist als nicht gegen aktuelle Releases geprüft markiert.
- **Der Build-Fortschritt zählt den laufenden Build mit** (veröffentlicht in 3.0.0): `get_build_status` sagte bei einer
  Anfrage mit einem Build 0 %, bis der Build endete, und nannte nur die Stufe der Anfrage. Prozent und Schritt
  enthalten jetzt den eigenen Fortschritt des laufenden Builds (Vorbereitung, Image, Modell, Module, Archiv); eine
  Anfrage, die noch läuft, sagt nie 100 %.

### Sicherheit
- **`install.sh` installiert Docker nur mit Prüfsumme** (DECISIONS Runde 23, Punkt 3): Fehlte Docker, lud der
  Installer `get.docker.com` herunter und führte es ungeprüft als root aus. Jetzt kommt Docker nur aus Dockers
  offiziellem apt-Repository unter Debian, Ubuntu und Raspberry Pi OS (32 Bit bekommt die armhf-Pakete von Debian,
  wie Dockers Anleitung es ab Engine 29 sagt). Systeme, die auf Ubuntu oder Debian aufbauen (`ID_LIKE`: Linux Mint,
  Pop!_OS, LMDE und ähnliche), die `get.docker.com` ebenfalls bediente, bekommen wie bisher die Pakete der Version,
  auf der sie aufbauen (`UBUNTU_CODENAME`; `DEBIAN_CODENAME` oder `/etc/debian_version`, das auch Debian testing
  seinen Codenamen gibt). Der Signaturschlüssel wird in einen Wegwerf-Schlüsselbund gelesen und nur vertraut, wenn
  der Download genau einen Schlüssel enthält, dessen Hauptfingerabdruck der aus Dockers Installationsanleitung ist
  (`9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`, mit Quelle fest im Skript); apt prüft danach jedes Paket
  gegen die signierten Paketlisten. Vor der Prüfung wird nichts geschrieben, und die Prüfung startet keinen
  gpg-agent, der den Installer überdauert. Eine apt-Quelle für Docker, die es schon gibt, bleibt, wie sie ist; eine
  auskommentierte oder mit `Enabled: no` (so hinterlässt sie ein Versions-Upgrade von Ubuntu) zählt nicht. Jedes
  andere System, ARMv6 (Raspberry Pi 1, Zero), ein Schlüssel, der nicht passt oder sich nicht laden lässt, und ein
  scheiterndes apt halten mit klaren Worten und dem Link zu Dockers Anleitung an; eine neue apt-Quelle, an der
  `apt-get update` scheitert, wird wieder entfernt. Kein heruntergeladenes Skript läuft mehr. Tests führen den
  Installer in einer Sandbox mit falschem curl, apt-get, dpkg und uname und einem echten, für den Test erzeugten
  gpg-Schlüssel aus; `install.sh` ist ohne ShellCheck-Befund (die beiden Befunde der Basis sind mit behoben).
- **Die Installer laden pip nicht mehr ohne Prüfsumme**: `install.sh` und `install.bat` führten vor der Installation
  mit Hash-Prüfung `pip install --upgrade pip` aus und luden damit das neueste pip ungeprüft vom Paketindex (unter
  Windows als Administrator). Die virtuelle Umgebung behält jetzt das pip, das mit Python kommt; es genügt für
  `--require-hashes --no-deps`, und der Installer-Test hält jeden pip-Aufruf am Export mit Hashes fest.
  `install.bat` lädt sonst nichts ungeprüft (winget prüft den Python-Installer gegen den SHA256 seines Manifests).
- **Lose Wissensdateien werden nicht mehr ohne die Paketprüfungen importiert** (DECISIONS Runde 23 Punkt 1): Jede
  `.json`-Datei in `community/knowledge/` und jede `knowledge.json` in einem Ordner ohne die Herkunftsdatei des Pakets
  wurde bei jedem Start in die Wissensbasis importiert, ohne Manifest, Signatur, Quarantäne oder Prüfung. Jetzt werden
  nur installierte Pakete importiert, und nur solange ihre Dateien dem Installierten entsprechen; eine lose Datei
  wartet, bis die Person sie über die Prüfungen eines installierten Pakets einsortiert. Eine Paketseite kann die Felder
  nicht mehr setzen, die der Import selbst setzt (`origin_pack`, `origin_sha256`, `topic`, `target`), und ein Thema
  oder Target erreicht einen KI-Client nur als schlichter Name.
- **Der neue Schlüssel eines neu gestarteten lokalen Modellservers geht nie an seinen alten Port** (Kette
  port-runtime, aus der Prüfung des verwalteten llama-server): Jeder Start gibt dem Server einen neuen Port und einen
  neuen Schlüssel. `LLMClient` bestimmte das Ziel (mit der URL) und baute danach den Adapter mit einem
  Anmeldenachweis, der den Zustand des Servers erneut las; ein Neustart dazwischen paarte daher die alte URL mit dem
  neuen Schlüssel und schickte ihn an das, was inzwischen auf dem alten Port lauschte. Der Adapter liest URL und
  Schlüssel des lokalen Servers jetzt einmal, zusammen, und lehnt ab, wenn sich die URL seit dem Bestimmen des Ziels
  geändert hat („the local model server was stopped or restarted; try again“, ein Ausfall für die Ersatzkette), sodass
  für den alten Port nichts gebaut oder geschickt wird (`orchestrator/llm/client.py` `adapter`; der
  Anmelde-Resolver nimmt diesen Schlüssel, `CredentialResolver.resolve(..., runtime_key=...)`). Regressionstest:
  `tests/test_runtime.py` `test_a_new_key_never_goes_to_the_port_of_the_previous_server`.
- **Ein anderes Programm auf dem Port des lokalen Modellservers bekommt nie dessen Schlüssel oder einen Prompt**
  (Kette port-runtime, aus der Prüfung des verwalteten llama-server): `runtime start` wählte einen freien Port,
  startete llama-server darauf und nahm jede Antwort von `/health` auf diesem Port für den Server. Ein Programm, das
  den Port zwischen der Wahl und dem Binden durch llama-server belegt hatte (ein anderer Prozess eines beliebigen
  Kontos auf diesem Computer), bekam dann von der Schlüsselprüfung den neuen Schlüssel, und der Endpunkt, den Ditto
  erhielt, zeigte auf es, sodass es auch die Prompts bekommen hätte. Eine Antwort zählt jetzt nur, solange
  llama-server selbst auf dem Port lauscht (seine Verbindungstabelle über psutil); sonst beendet sich llama-server
  sofort (es fand den Port belegt), der Start beendet es und versucht einen anderen Port, und an das andere Programm
  geht nichts. Wo sich die Verbindungstabelle nicht lesen lässt, hat ein Server, der 10 s weiterläuft, seinen Port
  gebunden und wird wie bisher genutzt. Nur Bare Metal: Im Docker-Modus bindet Docker den veröffentlichten Port
  selbst, und ein belegter Port lässt den Start scheitern. Review von port-runtime: `server.json` wird gleich nach
  dem Start geschrieben, und LLMClient in jedem anderen Prozess (die GUI, der MCP-Server) liest es, sodass ein Chat
  dort, solange die Prüfung noch lief, den Port des anderen Programms mit dem neuen Schlüssel bekam. Der Eintrag ist
  jetzt markiert (`"port_unconfirmed": true`), bis llama-server als Lauscher des Ports aufgeführt ist (es bindet,
  bevor es das Modell lädt, daher wird der Endpunkt weiter ausgegeben, während das Modell lädt), und `read_endpoint`
  gibt einen markierten Eintrag an niemanden; ein Start, der vor der Prüfung endete, hinterlässt einen Server, den
  der nächste Start beendet. Regressionstests: `tests/test_runtime.py`
  `test_a_program_answering_on_the_chosen_port_never_gets_the_key`,
  `test_an_unreliable_connection_table_does_not_block_the_start`,
  `test_other_processes_get_no_endpoint_before_the_port_is_confirmed`,
  `test_a_server_that_holds_its_port_is_handed_out_while_it_loads`,
  `test_a_start_that_died_before_the_port_was_confirmed_leaves_no_server_behind`.
- **Ein für einen Befehl gewählter Anbieter oder ein Modell wird nie zur gespeicherten Einstellung** (Übernahme aus
  dem behaltenen Worktree ditto-rewire-verified): `llm-cli ditto ask --provider/--model` und der Aufwand von `ditto
  eval` wurden als gewöhnliche Laufzeitwerte gesetzt, die `save_user_config` schreibt, also speicherte ein Speichern
  der Einstellungen während des Befehls (das Tool `update_settings`, das du bestätigst) die Wahl des einen Befehls als
  KI-Einstellung. Sie werden jetzt mit der Einstellungsquelle `cli` gesetzt, die nie gespeichert wird und die ein
  Neuladen aus `config.yml` nicht ersetzt (Grund `command_line`). Ein anderer Anbieter als der gespeicherte lässt für
  die Frage oder den Lauf auch Modell, Modelle je Aufgabe, Serveradresse und Anmeldeart des gespeicherten weg
  (`Core/ai_override.override_values(..., ditto=...)`, gemeinsam für CLI, Modul-Assistent, **Für diese Frage** im
  Chat-Bereich und **Ditto > Evaluation...**), sodass eine gespeicherte Ollama- oder eigene Adresse nie die Anfragen
  eines anderen Anbieters bekommt; ein Modell gilt für jede Aufgabe. Regressionstests: `tests/test_ditto.py`
  `test_one_command_provider_and_model_are_never_saved`, `test_the_eval_effort_is_never_saved`,
  `tests/test_ditto_models_core.py` `TestOneRunProviderSwitch`.
- **Ausgaben des Modells können das Terminal nicht steuern**: `llm-cli ditto ask` gab die Antwort, Fehlertexte, die
  einen Anbieter oder ein Tool zitieren, und Tool-Zeilen (ein erfundener Tool-Name, der Fehler eines Handlers) so aus,
  wie sie kamen, also konnten von einem Build-Log oder einer Webseite gesteuerte Escape-Sequenzen den Bildschirm, den
  Fenstertitel oder die Zwischenablage überschreiben. Steuerzeichen (C0 mit Wagenrücklauf, DEL, C1) und
  Formatzeichen (Bidi-Steuerzeichen, Nullbreitenzeichen) werden dort entfernt, ebenso aus der Zeile von
  `module generate-ai`, die sagt, dass das Basis-Image der KI nicht gepinnt werden kann; der Nullbreiten-Verbinder
  (Zero Width Joiner) in einer Emoji-Folge bleibt, wie bei der Bereinigung von Text von außen (`utils/untrusted`), und
  Umlaute, CJK und andere Schriften erscheinen, wie sie sind. Der Vorschlag von `module generate-ai` und seine Fakten
  zeigen versteckte Zeichen als Escapes (`tools.confirmation.visible`). Regressionstests: `tests/test_ditto.py`
  `test_ditto_ask_keeps_control_characters_of_the_model_out_of_the_terminal`,
  `test_the_terminal_text_keeps_every_script_and_emoji_as_they_are`,
  `test_generate_ai_keeps_control_characters_of_the_base_image_out_of_the_pin_error` und die Tests daneben.
- **Die Modellliste des KI-Einstellungsdialogs schickt nie einen Schlüssel an die Adresse des gespeicherten
  Anbieters**: Wer einen anderen Anbieter wählte, bevor er dessen Adresse eintippte (ein gespeichertes Ollama im
  lokalen Netz, dann **custom**), ließ die Modelle beim Server des gespeicherten Anbieters mit dem Schlüssel des
  anderen auflisten. Das Adressfeld zählt für die Liste jetzt immer, ein leeres bedeutet den eigenen Endpunkt des
  Anbieters, auch gegenüber der eigenen Adresse des Anbieters aus `llm-cli ditto login --base-url` (die das Feld zeigt
  und die das Speichern ersetzt), sodass ein für eine neue Adresse eingetippter Schlüssel nie an die alte geht.
  Regressionstests: `tests/test_dialogs.py` `test_model_list_never_sends_a_key_to_the_saved_providers_url`,
  `test_model_list_sends_a_typed_key_only_to_the_typed_address`.
- **Das Hugging-Face-Token geht nur an huggingface.co, auch wenn `HF_ENDPOINT` einen anderen Host nennt** (Review
  der Kette hf): huggingface_hub schickt seine API-Aufrufe an den Host in der Umgebungsvariable `HF_ENDPOINT` (einen
  Mirror). Die Commit-Abfrage des Downloads im GUI-Browser und die Hub-Liste von **Alte Downloads übernehmen…**
  schickten das eingegebene oder gespeicherte Token dorthin, ebenso jede Suche und Abfrage eines KI-Aufrufers,
  während der Fetcher es nur an huggingface.co schickt. Jeder Hub-API-Aufruf von `orchestrator/Core/hf_hub.py`
  (`HubClient`) und von Download und Übernahme des Browsers (`ModelManager`) läuft jetzt über `hf_hub.hub_api`, fest
  auf huggingface.co. Regressionstests: `tests/test_hf_gui_download.py` `TestTheTokenGoesOnlyToHuggingFace`.
- **Kein Hugging-Face-Aufruf im eigenen Code des Frameworks schickt mehr ein Token an den Host in `HF_ENDPOINT`
  oder ein implizites `HF_TOKEN`** (Konsolidierungskette hf-online). Außerhalb von `hf_hub` legten vier Aufrufer
  noch ihr eigenes `HfApi()` an: Das Bereitstellen eines Hugging-Face-Modells im Builder, die Abfrage des Build-Plans
  (`hf_lookup.look_up`) und die Commit-Prüfung der GUI für einen gleichen Build (`BuildService.model_commit`)
  schickten das im Framework gespeicherte Token an den Host in `HF_ENDPOINT`, und Suche, Lizenzprüfung und
  Dateiliste des Hugging-Face-Browsers (`ModelManager.search_huggingface_models`, `check_license`,
  `list_repo_files`) schickten das implizite Token (`HF_TOKEN` oder die Token-Datei im Hugging-Face-Home-Ordner)
  dorthin. Jeder von ihnen läuft jetzt über `hf_hub.hub_api` (nur huggingface.co; das Token, das die Person im
  Browser eingegeben hat, sonst das im Framework gespeicherte, sonst keines), und jede Dateiliste liest
  `hf_hub.hub_file` (`_hub_files` des Builders delegiert dorthin). Suche und Lizenzprüfung des Browsers nutzen das
  gespeicherte Token, wo sie das implizite nutzten. Einen Mirror in `HF_ENDPOINT` benutzt auch keiner dieser Aufrufe
  mehr, wie seit A6 die Downloads des Browsers; nur fastembed, die Embedding-Bibliothek der Wissensbasis, lädt ihre
  Modelle noch mit den eigenen Vorgaben von huggingface_hub (`HF_ENDPOINT`, ein implizites Token). Eine Umleitung
  einer API-Antwort auf einen anderen Host wird ohne das Token verfolgt. Regressionstests: `tests/test_hf_online.py`
  führt jeden Aufrufer durch den HTTP-Client von huggingface_hub selbst, mit `HF_ENDPOINT` auf einem anderen Host und
  gesetztem `HF_TOKEN`, und schlägt bei einem Token an diesen Host, beim impliziten Token, bei einem Token, das einer
  Umleitung auf einen anderen Host folgt, und bei einem `HfApi` außerhalb von `hf_hub` fehl.
- **Kein Home-Ordner und kein Name eines anderen Kontos erreicht eine KI über eine Datei-URL oder ein Leerzeichen**
  (D0; Vorgabe vom 2026-10-17). Die Pfad-Bezeichnungen für KIs (`security.paths.label_paths`, auch in `device_text`)
  ließen den Pfad einer `file:///`-URL stehen, wenn er den Home-Ordner eines anderen Kontos nannte
  (`file:///home/<Name>/...`, `file:///C:/Users/<Name>/...`, `file://localhost/...`, `file://server/Users/<Name>`); ihr
  Pfad wird jetzt wie jeder andere bezeichnet (der Dateiname, das Label eines bekannten Ordners, "a home folder"), und
  die URL behält ihr `file://`; ebenso eine Datei-URL einer Versionsverwaltung, wie pip und uv sie schreiben
  (`git+file:///home/<Name>/pkg`, auch `svn+`, `hg+`, `bzr+`), die ihr Schema behält. Ein Kontoname mit Leerzeichen
  wurde am Leerzeichen abgeschnitten ("a home folder Doe"):
  der eigene Kontoname dieses Computers wird jetzt nach jeder Wurzel ganz genommen, und nach dem Windows-Home-Ordner
  eines anderen Kontos gehören die folgenden Wörter, die mit einem Großbuchstaben oder einer Ziffer beginnen, zum
  Namen (ein POSIX-Kontoname hat kein Leerzeichen, so behält `/home/pi Failed` sein nächstes Wort).
- **Das GUI-Werkzeug verbirgt den Home-Ordner dieses Computers in jeder Schreibweise**: Ein Formularwert, den eine KI
  über das Tool `gui` liest (Ditto oder eine KI-App), zeigte den Home-Ordner nur dann als `~`, wenn er genau so
  geschrieben war, wie `Path.home()` ihn liefert. Über HOME oder USERPROFILE benannt (die MSYS-Form
  `/c/Users/<Name>`), mit dem anderen Trennzeichen, in anderer Groß- und Kleinschreibung (`c:/users/<Name>`) oder mit
  doppelten Backslashes, wie Python einen Pfad zitiert, erreichte er die KI mit dem Kontonamen. `tools_gui._no_home`
  nutzt jetzt das gemeinsame `security.paths.shorten_home` (dasselbe wie die Laufzeit- und Deploy-Tools) vor dem
  Muster für die Home-Ordner anderer Konten. Der Fehlerbericht (`utils/telemetry.py`, der Link zum GitHub-Issue)
  ersetzte nur `os.path.expanduser("~")` Buchstabe für Buchstabe; er ersetzt jetzt jede Schreibweise über dieselben
  Funktionen aus `security.paths` und behält seine Markierung `~USER`.
- **Jede Stelle nimmt dieselben SSH-Benutzernamen an**: Das Deployment (`llm-cli deploy run`, **Deploy to Target**
  im Deployment-Tab) und `llm-cli deploy devices add` prüften den Benutzernamen ohne `re.ASCII`, deshalb ließ Pythons
  Groß-/Kleinschreibungs-Faltung ein langes s (`ſ`) oder das Kelvin-Zeichen als `s` und `k` durch, und das `match()`
  des Deployments ließ auch einen Zeilenumbruch am Ende durch. Ein solches Deployment lief, und der vertrauenswürdige
  Eintrag des Boards lehnte denselben Namen danach wortlos ab, sodass KI-Tools das Board nicht nennen konnten. Eine
  Prüfung dient jetzt jeder Stelle (`orchestrator/remote/address.py`, `valid_user`: nur ASCII, der ganze Name, root
  nur, wo es erlaubt ist): dem Deployment, dem Hinzufügen eines Boards, dem Deployment-Tab, den Einträgen
  vertrauenswürdiger Boards, den Deploy-Einstellungen, der systemd-Unit und den Kontonamen, die eine KI nie liest
  (`device_text`). Das Hinzufügen eines Boards lädt für diese Prüfung nicht mehr die Build-Engine und das Docker-SDK.
  Der Board-Scan (`llm-cli scan --device`, **Remote Scan via SSH...** im Builder-Tab) prüft den Namen genauso, bevor
  das Board kontaktiert wird, mit der Ablehnung in der Sprache der Person; vorher erreichte ein solcher Name die
  Anmeldung des Boards.
- **Der Dialog der KI-Einstellungen lehnt eine Serveradresse mit Benutzername oder Passwort darin ab**, wie es die
  Anmeldung und `llmcf ditto login` schon taten (dieselbe Adressregel aus `AISettingsChange`): Vorher wurde eine
  solche Adresse (oder eine mit Leerzeichen oder Backslash, die Parser als zwei verschiedene Hosts lesen können) so,
  wie sie eingegeben war, in die `config.yml` gespeichert.
- **Ein Community-Rezept, das du in der GUI baust, wird beim Bauen geprüft, wie über MCP**: Der Knopf **Build** im
  Community Hub und **Start Build** im Builder-Tab gehen über `BuildService.installed_recipe` und
  `BuildService.start_recipe`, den Weg, den `start_recipe_build` nutzt. Ein Rezept, das der Community-Index
  zurückgezogen hat, nachdem die Liste des Hubs angezeigt wurde, oder eines, das sich nicht mehr lesen lässt, wird
  beim Klick und noch einmal bei Start Build abgelehnt; vorher stand nur der Build-Knopf im Weg, den die Liste
  deaktiviert hatte, und die GUI las die `recipe.yml` selbst. Ein Build, der alle Werte des Rezepts behält, legt
  jetzt seinen Kalibrierungsdatensatz fest: Eine andere Datei als die, deren sha256 das Rezept nennt, wird abgelehnt,
  und die Build-Engine prüft die Kopie, die sie nutzt. Ein Rezept, das sich geändert hat, seit es das Formular
  gefüllt hat, wird ebenfalls abgelehnt. Änderst du im Formular einen Wert des Rezepts, baust du weiter deine eigenen
  Werte auf dem Commit des Rezepts, nach derselben Prüfung auf Rückzug; ebenso, wie bisher, mit einem
  Kalibrierungsdatensatz für ein Rezept, das keinen nennt, oder ohne einen für ein Rezept, das einen nennt.
- **Text, den eine Leserin oder ein Leser nicht sieht, kommt nie in die Wissensbasis** (Recon P1): Text, den eine
  Webseite vor der lesenden Person versteckt (Inline-`display:none` oder `visibility:hidden`, eine Schriftgröße oder
  Deckkraft nahe null, Boxen außerhalb des Bildschirms oder weggeschnitten, Text in seiner eigenen Hintergrundfarbe,
  das Attribut `hidden`, `aria-hidden="true"`, `<template>`, `<noscript>` und ein CDATA-Abschnitt, den der Umwandler
  sogar zu Seitentext machte), kam unverändert in die Wissensbasis und mit ihm jede Anweisung an eine KI, die er
  enthielt. Deep Ingest lässt diese Elemente jetzt weg, bevor es Text und
  Links einer Seite liest (eine Regel, `hidden_kind` in `rag/html_text.py`, die der Schritt für Seitenelemente des
  Crawlers nutzt, also folgt der Crawler auch keinem Link, den niemand sieht); der inaktive Bereich eines Tab-Widgets
  bleibt. Eine Seite wird so gelesen, wie ein Browser sie liest: der Inline-Style mit `!important`, CSS-Kommentaren und
  Escapes, durchsichtiger Text (nicht Verlaufstext), `scale(0)`, `max-height: 0`, große negative Ränder und
  Verschiebungen, `filter: opacity(0)` und `content-visibility: hidden`; der Ersatztext von `<iframe>`, `<canvas>`,
  `<video>` und `<audio>`, `<noembed>`, `<noframes>`, `<datalist>`, `<rp>`, ein geschlossenes `<dialog>` und ein
  zweiter `<title>`; ein versteckendes Element als selbstschließender Tag (`<div hidden/>`) enthält, was danach folgt,
  und von einem doppelt geschriebenen Attribut zählt das erste. Jeder Text von außen wird danach schon so gereinigt
  gespeichert, wie ihn ein Modell zu sehen bekommt (NFKC, unsichtbare Zeichen, Tag-Zeichen und HTML-Kommentare
  entfernt; `utils/untrusted.clean_untrusted`, die bestehende Reinigung, die jetzt auch meldet, was sie entfernt hat),
  nicht erst beim Anzeigen gereinigt: gecrawlte Seiten und PDFs (`RAGManager.ingest_page`) und jede Seite eines
  Wissenspakets, deren HTML wie eine gecrawlte Seite gelesen wird oder, im Markdown, seine versteckten Elemente
  verliert (Code-Spans und Code-Blöcke bleiben; versteckt eine CommonMark-Darstellung des Ergebnisses noch Text, wird
  stattdessen der sichtbare Text dieser Darstellung gespeichert). Ein `<!--`, das nichts schließt, verliert in schon
  ausgelesenem Text nur diese Zeichen, nicht den Rest der Seite. Wie viel herausgefallen ist, Art für Art, steht im
  neuen Herkunftsfeld `hidden_removed` der Seite, das nur das Framework schreibt:
  **Tools > Wissensquellen...** und `llm-cli knowledge sources` zeigen es in der neuen Spalte „Ausgelassener
  versteckter Text“; es erreicht nie eine KI und geht nie in ein exportiertes Paket. Die eigenen Dokumente des
  Frameworks werden so gespeichert, wie sie geschrieben sind. Testseiten, die je eine Nutzlast verstecken
  (`evals/retrieval/carriers/`), laufen in den Tests durch den Crawler und die Wissensbasis und als eigene Gruppe in
  der nächtlichen Retrieval-Evaluation (`llm-cli ditto eval --kind retrieval --cases evals/retrieval/carriers.yml`),
  die scheitert, wenn eine Nutzlast in die gespeicherten Auszüge gelangt; die Evaluation liest ihr Korpus jetzt
  ebenfalls als Text von außen ein. Früher gespeicherte Seiten behalten ihre Auszüge, bis sie erneut eingelesen
  werden.
- **Ein Pull Request kann die Pflichtprüfung des Community-Repositorys nicht mehr umschreiben**: Die Vorlage des
  Prüf-Workflows `scripts/community_repo/validate-contribution.yml` lief bei `pull_request`, also führte ein Pull
  Request aus einem Fork seine eigene, geänderte Kopie aus: Er konnte die Prüfung bestehen lassen und ein schlechtes
  Pack hinzufügen, oder `.github/`, `index.json`, `packs/` oder eine README ändern, die die Prüfung nie ansah (sie
  listete nur `contributions/`). Die Maintainer-Aktionen lehnten einen solchen Pull Request ab, ein Merge auf der
  GitHub-Webseite wurde aber nicht verhindert. Die Prüfung läuft jetzt bei `pull_request_target` (opened,
  synchronize, reopened; Pull Requests in `main`), also prüft immer die Kopie in `main`, nur mit `contents: read`,
  ohne Secret, ohne Cache und mit `persist-credentials: false` in seinem einzigen Checkout, dem des Frameworks. Der
  Head-Commit des Pull Requests ist nur Daten und wird nie ausgecheckt (ein Checkout davon unter
  `pull_request_target` ist das bekannte gefährliche Muster, und actions/checkout gibt seinen Betreff aus, in dem ein
  Wagenrücklauf einen Workflow-Befehl beginnen würde): Nachdem das Framework installiert ist, werden `main` und der
  Head-Commit über seine ID still und ohne Zugangsdaten in ein Repository ohne Arbeitsverzeichnis geholt, nichts davon
  wird ausgeführt, eingelesen (`source`), installiert oder importiert, nur git liest es, und kein Wert des Pull
  Requests (Titel, Branch, Text, Commit-Nachrichten, Dateinamen) wird in ein `run:`-Skript geschrieben; er kommt über
  `env`. git berechnet die Änderungen zwischen `main` und dem Head (`--raw -z --no-renames`, ab ihrer einen
  Merge-Basis, so wie GitHub sie zusammenführt; ein Pull Request ohne oder mit mehr als einer Merge-Basis fällt
  durch, weil der Merge von GitHub dann Basen verbindet, die die Prüfung nicht sieht, und so eine Historie, die zwei
  Linien von `main` noch einmal zusammenführt, eine Veröffentlichung unbemerkt rückgängig machen könnte). Ein
  Dateiname mit einem Steuerzeichen, ein symbolischer Link (ein Link namens `x.zip` kann die
  Prüfung keine andere Datei mehr lesen lassen), eine ausführbare Datei oder ein Submodul fällt für jeden durch. Wer
  nicht Besitzer, Mitglied oder Collaborator ist (`author_association`), darf nur `contributions/<kind>/<name>.zip`
  hinzufügen, die Pfade, die die Maintainer-Aktionen prüfen; jede andere hinzugefügte, geänderte oder gelöschte Datei
  fällt mit ihrem Namen durch, die README eines Kind-Ordners eingeschlossen (sie bestand vorher als Hinweis). Die
  übrigen Änderungen des Besitzers, eines Mitglieds oder eines Collaborators sind Hinweise, deshalb bestehen die
  eigenen Pull Requests der Maintainer-Aktionen für Veröffentlichen und Zurückziehen weiter. Jedes hinzugefügte Pack
  wird geprüft wie bisher (höchstens 5 MB, `llm-cli zoo verify`), jetzt aus dem Blob des Head-Commits, und was zoo
  verify ausgibt, wird nie als Workflow-Befehl gelesen. Der Lint-Job der CI prüft die Vorlage auch mit actionlint.
  Tests führen die echten Schritte in bash gegen lokale git-Repositorys mit einem Stub für `llm-cli` aus (auch die
  Veröffentlichung der Maintainer-Aktionen über das nachgebildete GitHub, eine Commit-Nachricht mit Wagenrücklauf und
  eine Historie mit zwei Merge-Basen) und sichern den Aufbau des Workflows ab. Die Kopie im Community-Repository
  ändert dessen Maintainer (die Vorlage sagt, wie eine Kopie umgestellt wird, die noch bei `pull_request` läuft, und
  nennt die Einstellung, die das Repository außerdem braucht: Fork-Pull-Request-Workflows nur mit Freigabe, weil ein
  Pull Request immer noch einen eigenen Workflow mit einem Job namens `validate` mitbringen kann).
- **Abhängigkeiten auf den neuesten passenden Versionen, die Advisories vom Oktober 2026 behoben**: `uv lock
  --upgrade` hob jede Abhängigkeit auf die neueste Version, die ihr Bereich erlaubt (darunter pypdf 6.21.2, certifi
  2026.10.19, paramiko 5.1.0, qdrant-client 1.20.0, filelock 4.2.4), und Pillow geht auf 13.0.0, das 35 Advisories
  von 12.3.0 behebt. fastembed (bis 0.9.0) verlangt noch Pillow < 13; das Framework nutzt nur dessen Textmodelle und
  dekodiert nie ein Bild mit Pillow, deshalb hebt `pyproject.toml` diese Grenze auf (`[tool.uv]
  override-dependencies`), und Installer und CI installieren den vollständigen Export mit Hashes per `--no-deps`, damit
  pip ihn nicht neu auflöst: `requirements-gui.txt` (mit dem optionalen Extra `gui`), `requirements.txt` (ohne
  Bildschirm) und `requirements-dev.txt`, in den Installern, den Jobs für Tests, ohne GUI, Lint und Sicherheit, der
  nächtlichen Retrieval-Evaluation und der Workflow-Vorlage des Community-Repositorys. Ein Test meldet, sobald fastembed Pillow 13 selbst erlaubt und die Ausnahme entfallen
  kann. `astral-sh/setup-uv` in der CI geht auf v10.3.0 (Dependabot-PR #8). Geprüft mit der vollen Testsuite und einer
  echten Retrieval-Evaluation (88 % der Fälle bestanden).
  Am selben Tag danach: anthropic 1.15.0, openai 3.35.1, fastembed 0.9.0 (es begrenzt Pillow weiterhin, die Ausnahme
  bleibt), das Build-Backend uv_build bis 0.13 und die Ubuntu-22.04-Basis der GPU-Vorlage und des
  Build-Basis-Katalogs (`configs/llama_runtime.yml`) auf ihren aktuellen Digest, beide zusammen (Dependabot-PRs #10
  bis #12, von Hand übernommen: Dependabot hatte nur die Vorlage geändert und überlässt uns die Exporte mit Hashes).
  Dependabot öffnet seine Pull Requests jetzt gegen `development`, wo die Arbeit landet, statt gegen `main`.
- **Ein Basis-Image, das eine KI in den Modul-Assistenten einträgt, wird ebenfalls auf seinen Digest festgelegt**:
  `llm-cli module generate-ai` und das Tool `create_target_module` schreiben das Basis-Image einer KI mit dem Digest,
  auf den sein Tag zeigt, und der Assistent tat das für Dittos eigenen Vorschlag auch; ein Basis-Image, das eine KI
  über die GUI-Werkzeuge eingetragen hatte (Ditto im Chat oder eine KI-App), wurde als bloßes Tag wie `ubuntu:24.04`
  gespeichert. **Finish** schlägt den Digest jetzt außerhalb des GUI-Threads nach (dieselbe Abfrage,
  `ditto_manager.pin_reference`), zeigt das festgelegte Image und fragt zuerst („Nein“ ist die Vorgabe); das Modul
  wird über `DittoManager.save_module` mit genau diesem Image gespeichert. Lässt sich der Digest nicht nachschlagen
  (Offline-Modus, keine Registry), wird nichts gespeichert, und das Fenster sagt, warum und was zu tun ist. Das Feld
  behält den Wert der KI und ihre Markierung.
- **Eine URL für ein Quell-Repository nimmt kein Passwort und kein Token mehr auf**: `llm-cli config repos --add` und
  **Datei > Einstellungen… > Hinzufügen** speicherten eine URL genau wie eingegeben, deshalb landeten
  `https://user:passwort@...`, ein Token als Benutzername oder ein Parameter wie `?private_token=` als reiner Text in
  der `config.yml`. Beide lehnen eine solche URL jetzt mit einer Prüfung im Kern ab (`settings_view.url_credential` in
  `add_repo_override`; Exit-Code 2 in der CLI), mit einem einfachen Grund, der auf den Secrets-Speicher verweist
  (`llm-cli secrets set`, **Security > Geheimnisse...**); ein einfacher Kontoname wie der einer SSH-Adresse zählt
  nicht als Zugangsdaten. Ein früher so gespeicherter Eintrag erreicht die Builds weiter und wird nur mit
  `REDACTED` statt der Zugangsdaten gezeigt (die Liste, `config show`, Datei > Einstellungen…).
- **Der Modul-Assistent speichert Dittos Vorschlag so wie `llm-cli module generate-ai`**: Er speicherte ein von der
  KI erstelltes Modul nur über den Modul-Generator, deshalb fielen die Prüfungen von DittoManager.save_module weg (die
  Ablehnung jedes vorgefertigten `nvidia/cuda`-Images in einem KI-Vorschlag und die Pflicht zur Begründung der KI für
  ein Image, das kein Docker Official Image ist), und er legte das vorgeschlagene Basis-Image nicht auf seinen Digest
  fest. Jetzt wird das Basis-Image des Vorschlags im Analyse-Worker festgelegt, bevor du ihn siehst (`pin_base_image`,
  die Funktion der CLI), und das Modul wird über `DittoManager.save_module` gespeichert und wie auf der Kommandozeile
  im Audit-Log festgehalten; ein Basis-Image, das eine KI über die GUI-Werkzeuge in den Assistenten eingetragen hat,
  geht denselben Weg. Ein vorgefertigtes Image, das du selbst wählst, bleibt deine ausdrückliche Wahl. Ein
  regelbasierter Import einer Probe nach Dittos Vorschlag ersetzt jetzt auch Dittos Pakete, Setup-Befehle und
  Quantisierungslogik: Sie blieben in ihren Feldern und gelangten in ein Modul, das als deines gespeichert wurde, ohne
  Markierung und ohne Audit-Eintrag.
- **Das Koppeln eines Boards sendet nur eine geprüfte Schlüsselzeile** (bandit B601): Der Befehl, der den öffentlichen
  Schlüssel des Frameworks in `authorized_keys` des Boards einträgt, ist fest, und sein einziger Wert, die
  Schlüsselzeile, wurde für die Shell maskiert. Jetzt muss die Zeile außerdem ein reiner öffentlicher SSH-Schlüssel
  sein (Typ, Schlüssel, ein optionaler einfacher Kommentar); alles andere wird abgelehnt, bevor der Befehl gesendet
  wird.
- **`get_build_status` und `get_build_log` nennen fertige Builds über ihre Bezeichnung, nie über ihren Pfad**: in
  3.0.0 gaben der Status einer Build-Anfrage und Log-Zeilen wie „Golden Artifact created“ KI-Clients den vollen Pfad
  jedes fertigen Builds (mit dem Benutzernamen darin). Jetzt nennt der Status `output:<Target>/<Modell>/<Quantisierung>`
  (oder `builds:`, `saved:`, sonst einen Dateinamen), und eine Log-Zeile mit einem Pfad auf diesem Computer nennt die
  Ordner des Frameworks über ihre Bezeichnung und jeden anderen Pfad über seinen Dateinamen (auch unter `/home/`,
  `/media/`, `/Users/` und `C:\Users\`, zum Beispiel ein lokales Modell auf einem USB-Stick); deine eigenen Fenster
  behalten den vollen Pfad.
- **Kein Löschen ohne deine Bestätigung im Framework** (Runde 5 f; WP7): Jeder Codepfad, der ein gebautes Modell, einen
  Wissens-Snapshot, einen Papierkorb-Eintrag, ein Image des Frameworks, einen alten llama.cpp-Laufzeit-Build oder ein
  Laufzeit-Paket eines Boards löscht, nimmt nur den Plan, den du in der GUI oder im Terminal bestätigt hast, baut ihn unmittelbar vorher aus dem neu, was auf der Platte liegt, und löscht nichts,
  wenn er sich geändert hat oder das Audit-Log ihn nicht vorher festhalten kann. `delete_artifact` einer KI verschiebt
  nur in den Papierkorb; den Papierkorb leeren und endgültig löschen bleibt bei dir. Eine Prüfung des Quelltexts listet
  jede Stelle im Code, die löscht, mit ihrer Absicherung; eine neue Stelle lässt sie scheitern, bis über sie entschieden
  ist.
- **Gerätepasswörter gehören nicht mehr auf die Befehlszeile** (Design der sechsten Runde, 4.3): `deploy run`,
  `scan --device` und `device runtime deploy` fragen verdeckt, wenn `--password` keinen Wert hat, und
  `--password-stdin` liest eine Zeile aus einer Weiterleitung (in einem Terminal abgelehnt, wo das Passwort beim Tippen
  sichtbar wäre). Die Logger von paramiko bleiben während der Anmeldung auf INFO oder höher, sodass ein Passwort nie in
  ein Log gelangt, auch nicht bei DEBUG.
- **Der Trivy-Scan bekommt den Docker-Socket nicht mehr** (veröffentlicht in 3.0.0): Der Scanner-Container hatte den
  Docker-Socket des Rechners, der Root-Rechte auf diesem Rechner gibt, auch schreibgeschützt eingebunden. Der Builder
  speichert das Image jetzt als Archiv in einem privaten Ordner (0700) des Builds, und Trivy liest nur diese Datei,
  schreibgeschützt, ohne Capabilities und mit `no-new-privileges`. Das Log sagt, wann das Archiv gespeichert und wieder
  entfernt wird; ohne Platz dafür auf der Cache-Platte wird der Scan mit einer Warnung übersprungen, die die Größen
  nennt. Lehnt Docker den Scan ab, nennt die Warnung das Archiv mit der Bezeichnung des Cache-Ordners
  (`cache:...`), nie mit seinem Pfad auf diesem Rechner.
- **Der MCP-HTTP-Listener auf Loopback beachtet keine Proxy-Header und schreibt kein Zugriffsprotokoll:** uvicorn
  vertraute `X-Forwarded-For` und `X-Forwarded-Proto` von 127.0.0.1, sodass jedes lokale Programm eine andere Adresse
  vorgeben konnte. `mcp serve --transport http` läuft jetzt mit `proxy_headers=False` und `access_log=False`.
- **Der Installer sagt, was die Gruppe `docker` bedeutet:** `install.sh` nimmt dich weiter ohne Nachfrage in die
  Gruppe auf (siebte Runde, Punkt 6) und sagt jetzt klar, dass ihre Mitglieder den Docker-Dienst steuern, der als root
  läuft, sodass jedes Programm, das als dein Benutzer läuft, Root-Rechte bekommen kann; er nennt auch den Befehl, der es
  rückgängig macht.
- **`run_doctor` gibt KI-Clients keinen Pfad des Rechners** (veröffentlicht in 3.0.0): die Befunde, die eine KI las,
  nannten den persönlichen Ordner, das Audit-Log, Pack-Ordner, die Ordner der Speicherplatz-Prüfung und jeden Pfad,
  den der Fehler einer abgestürzten Prüfung zitierte, samt Benutzernamen. Was eine KI liest, nennt die Ordner des
  Frameworks jetzt unter ihrer Bezeichnung (`state:audit.db`, "the state folder", `targets:<Pack>`) und jeden anderen
  Pfad nur mit seinem Dateinamen, Geheimnisse geschwärzt; `llm-cli doctor` (auch `--json`) behält die vollen Pfade
  für dich.
- **`run_benchmark` startet genau die Datei, die es gehasht hat**: das Modell wird einmal geöffnet, über die offene
  Datei gehasht und llama-bench als genau diese offene Datei übergeben (nativ ein geerbter Deskriptor, in der
  Sandbox ein privater Hardlink), so dass ein Katalogmodell zwischen Prüfung und einem nativen Lauf nicht gegen ein
  anderes Modell ausgetauscht werden kann.
- **Fehler der Board- und lokalen Laufzeit-Tools nennen keinen Pfad des Rechners**: Pfade in ihren Meldungen
  erscheinen als Ordner-Bezeichnungen des Frameworks (`state:...`, `cache:...`, `output:...`, `models:...`) oder nur
  als Dateiname, nie als Pfad des Rechners oder `~/...` (auch nach einem Doppelpunkt wie in `file:/...` oder
  `user@board:/...` und in einer `file:///`-URL).
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
  entferntes Rechnen, Downloads, ein Prompt-Protokoll auf der Platte, ein Port, den ein anderer Prozess mitbenutzen
  dürfte (`--reuse-port`), ein Schlüssel oder Token auf der Befehlszeile, in jeder Schreibweise, die llama.cpp annimmt,
  auch `--a_b` für `--a-b`) und eine geleerte Umgebung, und jeder
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
  `LLM-Cross-Compiler-Framework-Bot` oder seinem früheren Namen `LLM-Conversion-Framework-Bot` erkannt (gleich in
  welcher Schreibweise), sodass eine Gruppe für `bot` nicht mehr über `*` siegt, und jedes Schema, jeder Host und
  jeder Port hat eine eigene robots.txt. Die Abweichung: Eine robots.txt, die mit 401 oder 403 antwortet, heißt
  „alles verboten“, wo RFC 9309 das Crawlen erlauben würde; ein Vorbehalt, den der Crawler nicht lesen kann, zählt
  trotzdem.
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
  alles. Eine ältere Kopie des Token-Speichers wird auch dann abgelehnt, wenn ihre Bytes genau die sind, die ein
  laufender MCP-Server oder die GUI vorher gelesen hat: Die gelesenen Tokens werden nach dem SHA-256 der Bytes der
  Datei zwischengespeichert, und bisher brachte eine solche Kopie das widerrufene Token in diesem Prozess zurück. Die
  Revision wird vor der Datei gelesen, daher sieht eine Token-Änderung, die zwischendurch in einem anderen Fenster
  gespeichert wird, nie wie eine ältere Kopie aus. Unter Linux und macOS wird eine ältere Kopie einer der beiden
  Dateien auch dann abgelehnt, wenn sie genau die Datei ist, die ein laufender Prozess vorher gelesen hat, über einen
  aufbewahrten Hardlink zurückgelegt (gleicher Inode, gleiche Größe und gleiche mtime): Die Revision im
  Schlüsselbund wird auch dann neu gelesen, wenn sich die ctime der Datei geändert hat. Ohne Schlüsselbund hat die
  Signatur keinen Schlüssel, wie die Audit-Kette; eine solche Datei wird abgelehnt, solange das Audit-Log mit
  Schlüssel geführt wird.
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
- **Der Kalibrierdatensatz eines Rezepts wird in einem Zug geprüft und benutzt**: `start_recipe_build` prüfte den
  sha256 des Datensatzes beim Festschreiben des Aufrufs, aber der Build öffnete den Pfad später erneut, sodass eine
  dazwischen ausgetauschte Datei benutzt wurde. Die Build-Engine öffnet die Datei jetzt einmal (nie über einen Link),
  kopiert ihre Bytes in den eigenen Ordner des Builds, während sie sie hasht, und benutzt nur diese Kopie, und nur wenn
  ihr sha256 der ist, den das Rezept festlegt (eine RKNN-Beispielliste behält ihren Ordner, die geprüfte Liste wird
  über ihren eigenen Namen gelegt).
- **Pfade mit Leerzeichen in Fehlern an eine KI oder die Person**: Ein Pfad mit einem Leerzeichen (ein Ordner wie
  `My Models` oder ein Home-Ordner, dessen Benutzername eines enthält) wurde am ersten Leerzeichen abgeschnitten,
  sodass die Bezeichnung falsch war und ein Stück des Benutzernamens oder des Host-Pfads übrig bleiben konnte. Pfade
  in Anführungszeichen, die Ordner des Frameworks und der Home-Ordner (auch unter seinem echten Pfad, wenn er ein
  Link ist) werden jetzt als Ganzes ersetzt, ein bekannter Ordner weiter hinten in einer Pfadliste
  (`/usr/bin:/home/...`) oder in einem Text in Anführungszeichen mit mehr als einem Pfad beginnt einen eigenen Pfad,
  und ein Leerzeichen setzt einen Pfad fort, wenn das nächste Wort klar dazugehört (oder, in einem bekannten Ordner,
  der längere Pfad existiert), sodass der Fehler nur eine Bezeichnung oder den Dateinamen nennt.

- **Kein Rich Text für nicht vertrauenswürdigen Text im Chat-Bereich und in den Fenstern, die er berührt**
  (Entwurf 5.2 R1-R7, SEC-M2): Jeder Text des Bereichs ist ein Label mit reinem Text (in `gui/chat_panel/` gibt es kein
  Rich-Text-Widget und keinen solchen Aufruf, ein Quelltext-Test prüft es, auch im Freigabe-Fenster, in den
  Einstellungsdialogen und im Hauptfenster); versteckte Zeichen erscheinen als Escape-Folgen; ein Wert von außerhalb
  des Frameworks steht in Anführungszeichen und bleibt in seiner Zeile, sodass ein Dateiname nicht als Zeile des
  Frameworks auftreten kann; nichts ist inline anklickbar (eine Adresse öffnet sich nur nach einer Frage, die sie ganz
  zeigt); das Aussehen einer App kommt aus ihrer Aufrufer-Kennung, nie aus ihrem eigenen Namen, und eine App, die sich
  Ditto, das Framework oder das System nennt, wird als „nennt sich so“ gezeigt. Nur Zeilen von Ditto bekommen
  Dittos Bild, und ein Hinweis darauf, was eine App getan hat, steht unter dem vertrauenswürdigen Namen dieser App;
  ein Werkzeugname, den sich Dittos Modell ausgedacht hat, steht in Anführungszeichen, nie als Worte des Frameworks; Guillemets in KI-Text erscheinen als Escape-Folgen, sodass
  nur das Framework einen sprechenden Namen «so» schreibt. Meldungsfenster, Tooltips,
  Desktop-Benachrichtigungen und Beschriftungen von Schaltflächen der neuen und geänderten Fenster laufen über
  `gui/plain.py` (Meldungsfenster mit reinem Text, maskierte Tooltips, Benachrichtigungen ohne Markup-Zeichen, kein
  Tastenkürzel aus einem „&“ in einem Namen); keine Taste auf einem Chat-Eintrag gibt etwas frei oder klickt etwas.

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
